from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
from types import ModuleType
from typing import Any

from ..config import AppConfig

logger = logging.getLogger(__name__)


class TranscriptAudioService:
    def __init__(self, config: AppConfig):
        self.config = config
        self._module: ModuleType | None = None

    @staticmethod
    def _parse_bool(raw: str | None, default: bool) -> bool:
        if raw is None:
            return default
        value = raw.strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
        return default

    def enabled(self) -> bool:
        return self._parse_bool(os.getenv("TRANSCRIPT_TTS_ENABLED"), True)

    def fail_on_error(self) -> bool:
        return self._parse_bool(os.getenv("TRANSCRIPT_TTS_FAIL_ON_ERROR"), False)

    def _load_module(self) -> ModuleType:
        if self._module is not None:
            return self._module

        module_path = Path(__file__).resolve().parents[2] / "transcript_to_audio.py"
        if not module_path.exists() or not module_path.is_file():
            raise RuntimeError(f"transcript_to_audio.py not found: {module_path}")

        spec = importlib.util.spec_from_file_location("transcript_to_audio_runtime", str(module_path))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load module spec for: {module_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        self._module = module
        return module

    def generate_for_job(
        self,
        job_id: str,
        *,
        tts_provider: str | None = None,
        tts_model: str | None = None,
        tts_voice: str | None = None,
        tts_elevenlabs_output_format: str | None = None,
    ) -> dict[str, Any]:
        module = self._load_module()
        load_dotenv_fn = getattr(module, "load_dotenv_file", None)
        if callable(load_dotenv_fn):
            dotenv_path = Path(__file__).resolve().parents[2] / ".env"
            load_dotenv_fn(dotenv_path)

        generate_fn = getattr(module, "generate_job_audio", None)
        if not callable(generate_fn):
            raise RuntimeError("transcript_to_audio.generate_job_audio is not available")

        default_provider = str(getattr(module, "DEFAULT_PROVIDER", "openai"))
        provider_raw = tts_provider or os.getenv("TRANSCRIPT_TTS_PROVIDER", default_provider)
        normalize_provider_fn = getattr(module, "normalize_provider", None)
        if callable(normalize_provider_fn):
            provider = str(normalize_provider_fn(provider_raw, default_provider))
        else:
            provider = provider_raw.strip().lower() or default_provider

        default_model_fn = getattr(module, "default_model_for_provider", None)
        if callable(default_model_fn):
            default_model = str(default_model_fn(provider))
        else:
            default_model = str(getattr(module, "DEFAULT_MODEL", "gpt-4o-mini-tts"))
        model = tts_model or os.getenv("TRANSCRIPT_TTS_MODEL", default_model)

        default_voice_fn = getattr(module, "default_voice_for_provider", None)
        if callable(default_voice_fn):
            default_voice = str(default_voice_fn(provider))
        else:
            default_voice = str(getattr(module, "DEFAULT_VOICE", "marin"))
        voice = tts_voice or os.getenv("TRANSCRIPT_TTS_VOICE", default_voice)

        elevenlabs_output_format = tts_elevenlabs_output_format or os.getenv(
            "TRANSCRIPT_TTS_ELEVENLABS_OUTPUT_FORMAT",
            str(getattr(module, "DEFAULT_ELEVENLABS_OUTPUT_FORMAT", "pcm_24000")),
        )

        default_max_chars = int(getattr(module, "DEFAULT_MAX_CHARS", 3500))
        max_chars_raw = os.getenv("TRANSCRIPT_TTS_MAX_CHARS", str(default_max_chars))
        try:
            max_chars = int(max_chars_raw)
        except Exception:
            max_chars = default_max_chars

        default_instructions = str(getattr(module, "DEFAULT_TTS_INSTRUCTIONS", ""))
        instructions = os.getenv("TRANSCRIPT_TTS_INSTRUCTIONS", default_instructions)
        include_slide_headings = not self._parse_bool(
            os.getenv("TRANSCRIPT_TTS_NO_SLIDE_HEADINGS"),
            False,
        )
        verbose = self._parse_bool(os.getenv("TRANSCRIPT_TTS_VERBOSE"), False)

        return generate_fn(
            job_id=job_id,
            jobs_root=self.config.jobs_root,
            neuronote_pipeline_root=self.config.neuronote_pipeline_root,
            provider=provider,
            model=model,
            voice=voice,
            max_chars=max_chars,
            include_slide_headings=include_slide_headings,
            instructions=instructions,
            elevenlabs_output_format=elevenlabs_output_format,
            artifact_roots=self.config.neuronote_artifact_roots,
            verbose=verbose,
        )
