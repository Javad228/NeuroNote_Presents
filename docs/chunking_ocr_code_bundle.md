# Chunking + OCR Code Bundle

Generated: 2026-02-17 07:15:05 UTC

## FILE: backend/services/chunking.py
```python
import sys
from pathlib import Path
from typing import Any, Optional

from .chunking_ocr_bridge import install_chunking_ocr_override


class ChunkingService:
    def __init__(
        self,
        *,
        chunking_root: Path,
        neuronote_pipeline_root: Path,
        azure_ocr_level: str,
        azure_ocr_min_conf: float,
    ):
        self.chunking_root = chunking_root
        self.neuronote_pipeline_root = neuronote_pipeline_root
        self.azure_ocr_level = azure_ocr_level
        self.azure_ocr_min_conf = azure_ocr_min_conf

    def _load_chunking_symbols(self):
        """Load chunking modules from the external chunking repository."""
        if not self.chunking_root.exists():
            raise RuntimeError(f"CHUNKING_ROOT does not exist: {self.chunking_root}")

        root_str = str(self.chunking_root)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)

        try:
            from src.chunking.base import chunks_to_json  # type: ignore
            from src.chunking.changepoint import changepoint_similarity_chunking  # type: ignore
            from src.pipeline import SlideChunkingPipeline  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "Failed to import chunking modules. Ensure chunking dependencies are installed "
                f"and CHUNKING_ROOT is correct. Root={self.chunking_root}"
            ) from exc

        return SlideChunkingPipeline, changepoint_similarity_chunking, chunks_to_json

    async def run_changepoint_chunking(
        self,
        *,
        slide_dir: Path,
        method: str,
        penalty: Optional[float],
        n_bkps: Optional[int],
        min_chunk: int,
        use_embeddings: bool,
        use_cache: bool,
    ) -> dict[str, Any]:
        """Run OCR+embedding extraction, then changepoint chunking."""
        (
            SlideChunkingPipeline,
            changepoint_similarity_chunking,
            chunks_to_json,
        ) = self._load_chunking_symbols()

        install_chunking_ocr_override(
            chunking_root=self.chunking_root,
            neuronote_pipeline_root=self.neuronote_pipeline_root,
            azure_level=self.azure_ocr_level,
            azure_min_conf=self.azure_ocr_min_conf,
        )

        pipeline = SlideChunkingPipeline(
            slide_dir=slide_dir,
            use_vision=False,
            use_cache=use_cache,
        )

        await pipeline.process_all_slides(show_progress=False)
        if not pipeline.slide_contents:
            raise RuntimeError(f"No slide images found under: {slide_dir / 'images'}")

        pipeline.compute_embeddings(show_progress=False)

        embeddings = [slide.embedding for slide in pipeline.slide_contents]
        chunks = changepoint_similarity_chunking(
            embeddings=embeddings,
            method=method,
            penalty=penalty,
            n_bkps=n_bkps,
            min_chunk=min_chunk,
            use_embeddings=use_embeddings,
        )

        result = {
            "num_slides": len(pipeline.slide_contents),
            "chunks": chunks_to_json(chunks),
        }

        pipeline_results = pipeline.get_results()
        if "similarities" in pipeline_results:
            result["adjacent_similarities"] = pipeline_results["similarities"]

        return result

    @staticmethod
    def _parse_slide_range(slide_range: Any) -> tuple[int, int]:
        """
        Normalize chunk slide range to (start, end).

        Supported formats:
        - [1, 3]
        - (1, 3)
        - "1-3"
        - "1 - 3"
        """
        if isinstance(slide_range, (list, tuple)) and len(slide_range) == 2:
            start = int(slide_range[0])
            end = int(slide_range[1])
            return start, end

        if isinstance(slide_range, str):
            normalized = slide_range.replace(" ", "")
            if "-" in normalized:
                left, right = normalized.split("-", 1)
                if left.isdigit() and right.isdigit():
                    return int(left), int(right)

        raise RuntimeError(f"Invalid slide_range: {slide_range}")

    @staticmethod
    def collect_chunk_images(images_dir: Path, slide_range: Any) -> list[Path]:
        start, end = ChunkingService._parse_slide_range(slide_range)
        if start > end:
            raise RuntimeError(f"Invalid slide range [{start}, {end}]")

        chunk_images: list[Path] = []
        for slide_num in range(start, end + 1):
            img_path = images_dir / f"page_{slide_num:03d}.png"
            if not img_path.exists():
                raise RuntimeError(f"Missing slide image for chunk: {img_path}")
            chunk_images.append(img_path)

        return chunk_images

```

## FILE: backend/services/chunking_ocr_bridge.py
```python
import sys
from pathlib import Path
from typing import Callable


def build_azure_ocr_extractor(
    *,
    neuronote_pipeline_root: Path,
    azure_level: str,
    azure_min_conf: float,
) -> Callable[[Path], str]:
    """Build an OCR extractor that uses NeuroNote's Azure OCR backend."""
    root_str = str(neuronote_pipeline_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    try:
        from neuronote.extraction.ocr.dispatch import load_ocr_engine, run_ocr  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Failed to import NeuroNote OCR modules. "
            f"Expected package under: {neuronote_pipeline_root}"
        ) from exc

    engine = load_ocr_engine(backend="azure")

    def extract_text(image_path: Path) -> str:
        detections = run_ocr(
            str(image_path),
            engine,
            backend="azure",
            azure_level=azure_level,
            azure_min_conf=azure_min_conf,
        )

        if not detections:
            return ""

        items = []
        for det in detections:
            text = str(det.get("text_content", "")).strip()
            bbox = det.get("bbox") or [0, 0, 0, 0]
            if not text:
                continue
            try:
                x1, y1, x2, y2 = [float(v) for v in bbox]
            except Exception:
                x1, y1, x2, y2 = 0.0, 0.0, 0.0, 0.0
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            items.append((cy, cx, text))

        if not items:
            return ""

        items.sort(key=lambda value: (value[0], value[1]))
        return "\n".join(text for _, _, text in items)

    return extract_text


def install_chunking_ocr_override(
    *,
    chunking_root: Path,
    neuronote_pipeline_root: Path,
    azure_level: str,
    azure_min_conf: float,
) -> None:
    """Replace chunking repo OCR with Azure OCR implementation."""
    root_str = str(chunking_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    try:
        from src import ocr as chunking_ocr  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            f"Failed to import chunking OCR module from {chunking_root}"
        ) from exc

    extractor = build_azure_ocr_extractor(
        neuronote_pipeline_root=neuronote_pipeline_root,
        azure_level=azure_level,
        azure_min_conf=azure_min_conf,
    )

    chunking_ocr.extract_text_from_image = extractor

```

## FILE: backend/services/orchestrator.py
```python
import asyncio
import json
import logging
import time
import uuid
from typing import Any

from ..config import AppConfig
from ..schemas import ProcessPdfOptions
from .gcs_upload import GCSUploadService
from .neuronote_client import NeuroNoteClient
from .pdf_render import render_pdf_to_images
from .transcript_audio import TranscriptAudioService

logger = logging.getLogger(__name__)


class OrchestratorService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.gcs_upload_service = GCSUploadService(
            bucket_name=config.gcs_images_bucket,
            object_prefix=config.gcs_images_prefix,
        )
        self.transcript_audio_service = TranscriptAudioService(config)

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            return int(value)
        except Exception:
            return None

    @staticmethod
    def _collect_object_paths(
        uploaded_images: list[dict[str, Any]],
    ) -> list[str]:
        object_paths: list[str] = []
        for item in uploaded_images:
            if not isinstance(item, dict):
                continue
            object_path = item.get("object_path")
            if not isinstance(object_path, str):
                continue
            value = object_path.strip()
            if not value:
                continue
            object_paths.append(value)

        if not object_paths:
            raise RuntimeError("Image upload succeeded but no object paths were returned.")

        return object_paths

    @classmethod
    def _build_chunk_manifest_from_status(
        cls,
        status_payload: dict[str, Any],
        *,
        fallback_num_slides: int,
    ) -> list[dict[str, Any]]:
        result_payload = status_payload.get("result")
        if not isinstance(result_payload, dict):
            result_payload = {}

        manifest: list[dict[str, Any]] = []
        raw_chunks = result_payload.get("chunks")
        if isinstance(raw_chunks, list):
            for raw_chunk in raw_chunks:
                if not isinstance(raw_chunk, dict):
                    continue
                chunk_index = cls._safe_int(raw_chunk.get("chunk_index"))
                if chunk_index is None or chunk_index < 0:
                    continue
                image_count = cls._safe_int(raw_chunk.get("image_count"))
                if image_count is None:
                    raw_images = raw_chunk.get("images")
                    image_count = len(raw_images) if isinstance(raw_images, list) else 0
                manifest.append(
                    {
                        "chunk_index": chunk_index,
                        "chunk_id": str(raw_chunk.get("chunk_id") or f"chunk_{chunk_index + 1:03d}"),
                        "slide_range": raw_chunk.get("slide_range"),
                        "num_slides": max(0, image_count),
                    }
                )

        if manifest:
            manifest.sort(key=lambda item: item["chunk_index"])
            return manifest

        return [
            {
                "chunk_index": 0,
                "chunk_id": "chunk_001",
                "slide_range": f"1-{max(1, fallback_num_slides)}",
                "num_slides": max(1, fallback_num_slides),
            }
        ]

    @classmethod
    def _group_images_by_chunk_index(cls, result_payload: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
        grouped: dict[int, list[dict[str, Any]]] = {}
        raw_chunks = result_payload.get("chunks")
        if isinstance(raw_chunks, list):
            for raw_chunk in raw_chunks:
                if not isinstance(raw_chunk, dict):
                    continue
                chunk_index = cls._safe_int(raw_chunk.get("chunk_index"))
                if chunk_index is None or chunk_index < 0:
                    continue
                images = raw_chunk.get("images")
                if not isinstance(images, list):
                    images = []
                grouped[chunk_index] = [img for img in images if isinstance(img, dict)]

        if grouped:
            return grouped

        raw_images = result_payload.get("images")
        if isinstance(raw_images, list):
            for raw_image in raw_images:
                if not isinstance(raw_image, dict):
                    continue
                chunk_index = cls._safe_int(raw_image.get("chunk_index"))
                if chunk_index is None or chunk_index < 0:
                    continue
                grouped.setdefault(chunk_index, []).append(raw_image)

        return grouped

    @classmethod
    def _chunk_details_by_index(cls, result_payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
        details: dict[int, dict[str, Any]] = {}
        raw_chunks = result_payload.get("chunks")
        if not isinstance(raw_chunks, list):
            return details
        for raw_chunk in raw_chunks:
            if not isinstance(raw_chunk, dict):
                continue
            chunk_index = cls._safe_int(raw_chunk.get("chunk_index"))
            if chunk_index is None or chunk_index < 0:
                continue
            details[chunk_index] = raw_chunk
        return details

    def _build_legacy_neuronote_chunks(
        self,
        *,
        chunk_manifest: list[dict[str, Any]],
        status_payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        result_payload = status_payload.get("result")
        if not isinstance(result_payload, dict):
            result_payload = {}

        images_by_chunk = self._group_images_by_chunk_index(result_payload)
        chunk_details = self._chunk_details_by_index(result_payload)
        batch_id = (
            result_payload.get("batch_id")
            or status_payload.get("batch_id")
            or status_payload.get("job_id")
        )
        out: list[dict[str, Any]] = []

        for item in chunk_manifest:
            chunk_index = item["chunk_index"]
            detail = chunk_details.get(chunk_index, {})
            chunk_result: dict[str, Any] = {
                "batch_id": batch_id,
                "images": images_by_chunk.get(chunk_index, []),
            }
            for key in ("chunk_index", "image_count", "successful", "failed"):
                if key in detail:
                    chunk_result[key] = detail[key]

            chunk_id = item.get("chunk_id")
            if not isinstance(chunk_id, str) or not chunk_id:
                chunk_id = f"chunk_{chunk_index + 1:03d}"

            out.append(
                {
                    "chunk_id": chunk_id,
                    "slide_range": item.get("slide_range"),
                    "num_slides": item.get("num_slides"),
                    "endpoint_used": "/api/process-batch-gcs",
                    "neuronote": {
                        "status": status_payload.get("status"),
                        "job_id": status_payload.get("job_id"),
                        "result": chunk_result,
                    },
                }
            )

        return out

    async def process_pdf(
        self,
        *,
        pdf_bytes: bytes,
        options: ProcessPdfOptions,
    ) -> dict[str, Any]:
        process_started = time.perf_counter()
        job_id = uuid.uuid4().hex[:12]
        job_dir = self.config.jobs_root / job_id
        slide_dir = job_dir / "slides"
        images_dir = slide_dir / "images"
        labels_dir = slide_dir / "labels"

        job_dir.mkdir(parents=True, exist_ok=False)
        labels_dir.mkdir(parents=True, exist_ok=True)

        pdf_path = job_dir / "input.pdf"
        pdf_path.write_bytes(pdf_bytes)

        page_count = render_pdf_to_images(pdf_path, images_dir, dpi=options.render_dpi)

        gcs_folder = self.gcs_upload_service.folder_for_job(job_id)
        bucket_path = self.gcs_upload_service.bucket_path_for_job(job_id)
        logger.info(
            "[gcs] upload_start job=%s bucket_path=%s",
            job_id,
            bucket_path,
        )
        gcs_upload_started = time.perf_counter()
        try:
            uploaded_images = await self.gcs_upload_service.upload_images_async(
                job_id=job_id,
                images_dir=images_dir,
            )
        except Exception as exc:
            logger.exception(
                "[gcs] upload_fail job=%s bucket_path=%s",
                job_id,
                bucket_path,
            )
            raise RuntimeError(f"Failed uploading slide images to {bucket_path}: {exc}") from exc

        gcs_elapsed_s = time.perf_counter() - gcs_upload_started
        logger.info(
            "[gcs] upload_done job=%s uploaded=%d elapsed_s=%.2f",
            job_id,
            len(uploaded_images),
            gcs_elapsed_s,
        )

        object_paths = self._collect_object_paths(uploaded_images)

        async with NeuroNoteClient(
            base_url=self.config.neuronote_api_base,
            timeout_seconds=self.config.neuronote_timeout_seconds,
        ) as neuronote:
            submit_response = await neuronote.process_batch_gcs_images(
                bucket_path=bucket_path,
                object_paths=object_paths,
                skip_generation=options.skip_generation,
            )
            upstream_job_id = str(
                submit_response.get("job_id") or submit_response.get("batch_id") or ""
            ).strip()
            if not upstream_job_id:
                raise RuntimeError(
                    "NeuroNote /api/process-batch-gcs response is missing job_id/batch_id."
                )

            poll_interval = max(0.2, self.config.neuronote_poll_interval_seconds)
            status_payload = await neuronote.wait_for_job(
                job_id=upstream_job_id,
                timeout_seconds=self.config.neuronote_timeout_seconds,
                poll_interval_seconds=poll_interval,
            )

        upstream_status = str(status_payload.get("status", "")).lower()
        if upstream_status != "complete":
            error_detail = (
                status_payload.get("error")
                or status_payload.get("detail")
                or status_payload.get("message")
                or status_payload
            )
            logger.error(
                "[orchestrator] upstream_fail job=%s upstream_job=%s status=%s detail=%s",
                job_id,
                status_payload.get("job_id"),
                upstream_status or "unknown",
                error_detail,
            )
            raise RuntimeError(
                f"NeuroNote batch job {status_payload.get('job_id', 'unknown')} "
                f"finished with status '{upstream_status or 'unknown'}': {error_detail}"
            )

        chunk_manifest = self._build_chunk_manifest_from_status(
            status_payload,
            fallback_num_slides=len(object_paths),
        )
        neuronote_chunks = self._build_legacy_neuronote_chunks(
            chunk_manifest=chunk_manifest,
            status_payload=status_payload,
        )

        transcript_audio: dict[str, Any] = {
            "enabled": self.transcript_audio_service.enabled(),
            "status": "skipped",
        }

        final_result = {
            "job_id": job_id,
            "working_dir": str(job_dir),
            "input_pdf": str(pdf_path),
            "page_count": page_count,
            "gcs_upload": {
                "bucket": self.config.gcs_images_bucket,
                "folder": gcs_folder,
                "bucket_path": bucket_path,
                "uploaded_count": len(uploaded_images),
                "uploaded_images": uploaded_images,
            },
            "chunking": {
                "source": "upstream",
                "num_slides": len(object_paths),
                "chunks": [
                    {
                        "chunk_id": item["chunk_id"],
                        "slide_range": item.get("slide_range"),
                    }
                    for item in chunk_manifest
                ],
                "requested_options": {
                    "method": options.method,
                    "penalty": options.penalty,
                    "n_bkps": options.n_bkps,
                    "min_chunk": options.min_chunk,
                    "use_embeddings": options.use_embeddings,
                    "use_cache": options.use_cache,
                },
            },
            "neuronote_job": {
                "submit_response": submit_response,
                "final_status": status_payload,
            },
            "neuronote_chunks": neuronote_chunks,
            "transcript_audio": transcript_audio,
        }

        result_path = job_dir / "result.json"
        result_path.write_text(json.dumps(final_result, indent=2))

        if transcript_audio["enabled"]:
            audio_started = time.perf_counter()
            logger.info("[audio] generation_start job=%s", job_id)
            try:
                audio_meta = await asyncio.to_thread(
                    self.transcript_audio_service.generate_for_job,
                    job_id,
                )
                audio_elapsed_s = time.perf_counter() - audio_started
                transcript_audio = {
                    "enabled": True,
                    "status": "complete",
                    "elapsed_s": round(audio_elapsed_s, 3),
                    **audio_meta,
                }
                logger.info(
                    "[audio] generation_done job=%s steps=%s elapsed_s=%.2f",
                    job_id,
                    audio_meta.get("steps"),
                    audio_elapsed_s,
                )
            except Exception as exc:
                logger.exception("[audio] generation_fail job=%s", job_id)
                transcript_audio = {
                    "enabled": True,
                    "status": "error",
                    "error": str(exc),
                }
                if self.transcript_audio_service.fail_on_error():
                    final_result["transcript_audio"] = transcript_audio
                    result_path.write_text(json.dumps(final_result, indent=2))
                    raise RuntimeError(f"Transcript audio generation failed: {exc}") from exc

            final_result["transcript_audio"] = transcript_audio
            result_path.write_text(json.dumps(final_result, indent=2))

        total_elapsed_s = time.perf_counter() - process_started
        logger.info(
            "[orchestrator] process_done job=%s upstream_job=%s chunks=%d elapsed_s=%.2f",
            job_id,
            status_payload.get("job_id"),
            len(chunk_manifest),
            total_elapsed_s,
        )

        return final_result

```

## FILE: backend/services/transcript_audio.py
```python
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

    def generate_for_job(self, job_id: str) -> dict[str, Any]:
        module = self._load_module()
        generate_fn = getattr(module, "generate_job_audio", None)
        if not callable(generate_fn):
            raise RuntimeError("transcript_to_audio.generate_job_audio is not available")

        model = os.getenv("TRANSCRIPT_TTS_MODEL", str(getattr(module, "DEFAULT_MODEL", "gpt-4o-mini-tts")))
        voice = os.getenv("TRANSCRIPT_TTS_VOICE", str(getattr(module, "DEFAULT_VOICE", "marin")))

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
            model=model,
            voice=voice,
            max_chars=max_chars,
            include_slide_headings=include_slide_headings,
            instructions=instructions,
            artifact_roots=self.config.neuronote_artifact_roots,
            verbose=verbose,
        )


```

## FILE: backend/services/neuronote_client.py
```python
import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class NeuroNoteClient:
    def __init__(self, base_url: str, timeout_seconds: float):
        self.base_url = base_url.rstrip("/")
        timeout = httpx.Timeout(timeout_seconds, connect=30.0)
        self.client = httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.client.aclose()

    async def process_single(
        self,
        image_path: Path,
        *,
        skip_generation: bool,
        previous_context: Optional[str],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        url = f"{self.base_url}/api/process"
        logger.info(
            "[neuronote] request_start endpoint=/api/process image=%s skip_generation=%s",
            image_path.name,
            skip_generation,
        )
        files = {
            "image": (image_path.name, image_path.read_bytes(), "image/png"),
        }
        data: dict[str, str] = {}
        if previous_context:
            data["previous_context"] = previous_context

        response = await self.client.post(
            url,
            params={"sync": "true", "skip_generation": str(skip_generation).lower()},
            data=data,
            files=files,
        )
        elapsed_s = time.perf_counter() - started
        if response.status_code >= 400:
            logger.error(
                "[neuronote] request_fail endpoint=/api/process image=%s status=%d elapsed_s=%.2f",
                image_path.name,
                response.status_code,
                elapsed_s,
            )
            raise RuntimeError(
                f"NeuroNote single API returned {response.status_code}: {response.text[:400]}"
            )
        logger.info(
            "[neuronote] request_done endpoint=/api/process image=%s status=%d elapsed_s=%.2f",
            image_path.name,
            response.status_code,
            elapsed_s,
        )
        return response.json()

    async def process_batch(
        self,
        image_paths: list[Path],
        *,
        skip_generation: bool,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        url = f"{self.base_url}/api/process-batch"
        logger.info(
            "[neuronote] request_start endpoint=/api/process-batch images=%d skip_generation=%s",
            len(image_paths),
            skip_generation,
        )
        files = [
            ("images", (image_path.name, image_path.read_bytes(), "image/png"))
            for image_path in image_paths
        ]

        response = await self.client.post(
            url,
            params={"sync": "true", "skip_generation": str(skip_generation).lower()},
            files=files,
        )
        elapsed_s = time.perf_counter() - started
        if response.status_code >= 400:
            logger.error(
                "[neuronote] request_fail endpoint=/api/process-batch images=%d status=%d elapsed_s=%.2f",
                len(image_paths),
                response.status_code,
                elapsed_s,
            )
            raise RuntimeError(
                f"NeuroNote batch API returned {response.status_code}: {response.text[:400]}"
            )
        logger.info(
            "[neuronote] request_done endpoint=/api/process-batch images=%d status=%d elapsed_s=%.2f",
            len(image_paths),
            response.status_code,
            elapsed_s,
        )
        return response.json()

    async def process_batch_gcs(
        self,
        *,
        bucket_path: str,
        chunks: list[list[str]],
        skip_generation: bool,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        url = f"{self.base_url}/api/process-batch-gcs"
        logger.info(
            "[neuronote] request_start endpoint=/api/process-batch-gcs bucket_path=%s chunks=%d",
            bucket_path,
            len(chunks),
        )

        payload = {
            "bucket_path": bucket_path,
            "chunks": chunks,
            "skip_generation": skip_generation,
        }
        response = await self.client.post(url, json=payload)
        elapsed_s = time.perf_counter() - started
        if response.status_code >= 400:
            logger.error(
                "[neuronote] request_fail endpoint=/api/process-batch-gcs status=%d elapsed_s=%.2f",
                response.status_code,
                elapsed_s,
            )
            raise RuntimeError(
                f"NeuroNote GCS batch API returned {response.status_code}: {response.text[:400]}"
            )

        logger.info(
            "[neuronote] request_done endpoint=/api/process-batch-gcs status=%d elapsed_s=%.2f",
            response.status_code,
            elapsed_s,
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("NeuroNote GCS batch API returned a non-object payload.")
        return payload

    async def process_batch_gcs_images(
        self,
        *,
        bucket_path: str,
        object_paths: list[str],
        skip_generation: bool,
    ) -> dict[str, Any]:
        """Submit object paths directly; upstream performs chunking."""
        cleaned = [str(path).strip() for path in object_paths if str(path).strip()]
        if not cleaned:
            raise RuntimeError("No image object paths were provided for GCS batch processing.")

        started = time.perf_counter()
        url = f"{self.base_url}/api/process-batch-gcs"
        logger.info(
            "[neuronote] request_start endpoint=/api/process-batch-gcs bucket_path=%s object_paths=%d",
            bucket_path,
            len(cleaned),
        )
        payload = {
            "bucket_path": bucket_path,
            "object_paths": cleaned,
            "skip_generation": skip_generation,
        }
        response = await self.client.post(url, json=payload)
        elapsed_s = time.perf_counter() - started
        if response.status_code >= 400:
            logger.error(
                "[neuronote] request_fail endpoint=/api/process-batch-gcs status=%d elapsed_s=%.2f",
                response.status_code,
                elapsed_s,
            )
            raise RuntimeError(
                f"NeuroNote GCS batch API returned {response.status_code}: {response.text[:400]}"
            )

        logger.info(
            "[neuronote] request_done endpoint=/api/process-batch-gcs status=%d elapsed_s=%.2f",
            response.status_code,
            elapsed_s,
        )
        result_payload = response.json()
        if not isinstance(result_payload, dict):
            raise RuntimeError("NeuroNote GCS batch API returned a non-object payload.")
        return result_payload

    async def get_job(self, *, job_id: str) -> dict[str, Any]:
        started = time.perf_counter()
        url = f"{self.base_url}/api/jobs/{job_id}"
        response = await self.client.get(url)
        elapsed_s = time.perf_counter() - started
        if response.status_code >= 400:
            raise RuntimeError(
                f"NeuroNote job status API returned {response.status_code}: {response.text[:400]}"
            )

        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("NeuroNote job status API returned a non-object payload.")

        logger.debug(
            "[neuronote] poll_done endpoint=/api/jobs/%s status=%d elapsed_s=%.2f",
            job_id,
            response.status_code,
            elapsed_s,
        )
        return payload

    async def wait_for_job(
        self,
        *,
        job_id: str,
        timeout_seconds: float,
        poll_interval_seconds: float = 2.0,
    ) -> dict[str, Any]:
        poll_every = max(0.2, poll_interval_seconds)
        deadline = time.perf_counter() + max(1.0, timeout_seconds)
        last_status = ""
        logger.info(
            "[neuronote] poll_start job=%s timeout_s=%.1f interval_s=%.1f",
            job_id,
            timeout_seconds,
            poll_every,
        )

        while True:
            try:
                status_payload = await self.get_job(job_id=job_id)
            except RuntimeError as exc:
                message = str(exc)
                if "returned 404" in message and time.perf_counter() < deadline:
                    await asyncio.sleep(poll_every)
                    continue
                raise
            status = str(status_payload.get("status", "")).lower()

            if status != last_status:
                logger.info(
                    "[neuronote] poll_status job=%s status=%s completed_chunks=%s completed_images=%s",
                    job_id,
                    status or "unknown",
                    status_payload.get("completed_chunks"),
                    status_payload.get("completed_images"),
                )
                last_status = status

            if status in {"complete", "error", "failed", "cancelled"}:
                return status_payload

            if time.perf_counter() >= deadline:
                raise RuntimeError(
                    f"Timed out waiting for NeuroNote job {job_id} (last status={status or 'unknown'})."
                )

            await asyncio.sleep(poll_every)

```

## FILE: backend/config.py
```python
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    chunking_root: Path
    neuronote_pipeline_root: Path
    neuronote_artifact_roots: list[Path]
    neuronote_api_base: str
    jobs_root: Path
    default_render_dpi: int
    neuronote_timeout_seconds: float
    neuronote_poll_interval_seconds: float
    max_pdf_size_mb: int
    azure_ocr_level: str
    azure_ocr_min_conf: float
    cors_allow_origins: list[str]
    gcs_images_bucket: str
    gcs_images_prefix: str


def _parse_cors_allow_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _parse_artifact_roots(raw: str) -> list[Path]:
    if not raw:
        return []

    # Accept comma-separated and os.pathsep-separated lists.
    tokens: list[str] = []
    for part in raw.split(","):
        tokens.extend(part.split(os.pathsep))

    roots: list[Path] = []
    seen: set[str] = set()
    for token in tokens:
        item = token.strip()
        if not item:
            continue
        try:
            resolved = Path(item).expanduser().resolve()
        except Exception:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)

    return roots


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    base_dir = Path(__file__).resolve().parent.parent
    return AppConfig(
        chunking_root=Path(
            os.environ.get("CHUNKING_ROOT", "/home/javad/NeuroNote/chunking_slides_v2")
        ).resolve(),
        neuronote_pipeline_root=Path(
            os.environ.get("NEURONOTE_PIPELINE_ROOT", "/home/javad/NeuroNote_Pipeline")
        ).resolve(),
        neuronote_artifact_roots=_parse_artifact_roots(
            os.environ.get("NEURONOTE_ARTIFACT_ROOTS", "")
        ),
        neuronote_api_base=os.environ.get("NEURONOTE_API_BASE", "http://127.0.0.1:8000").rstrip("/"),
        jobs_root=Path(os.environ.get("JOBS_ROOT", str(base_dir / "jobs"))).resolve(),
        default_render_dpi=int(os.environ.get("RENDER_DPI", "200")),
        neuronote_timeout_seconds=float(os.environ.get("NEURONOTE_TIMEOUT_SECONDS", "3600")),
        neuronote_poll_interval_seconds=float(os.environ.get("NEURONOTE_POLL_INTERVAL_SECONDS", "2.0")),
        max_pdf_size_mb=int(os.environ.get("MAX_PDF_SIZE_MB", "100")),
        azure_ocr_level=os.environ.get("AZURE_OCR_LEVEL", "lines"),
        azure_ocr_min_conf=float(os.environ.get("AZURE_OCR_MIN_CONF", "0.0")),
        cors_allow_origins=_parse_cors_allow_origins(
            os.environ.get("CORS_ALLOW_ORIGINS", "")
        ),
        gcs_images_bucket=os.environ.get("GCS_IMAGES_BUCKET", "lectura-images").strip(),
        gcs_images_prefix=os.environ.get("GCS_IMAGES_PREFIX", "").strip().strip("/"),
    )

```

## FILE: backend/schemas.py
```python
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProcessPdfOptions:
    method: str
    penalty: Optional[float]
    n_bkps: Optional[int]
    min_chunk: int
    use_embeddings: bool
    use_cache: bool
    skip_generation: bool
    previous_context: Optional[str]
    render_dpi: int

```

## FILE: backend/api/routes.py
```python
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response

from ..config import AppConfig, get_config
from ..services.lecture import LectureService
from ..services.jobs import JobsService
from ..schemas import ProcessPdfOptions
from ..services.orchestrator import OrchestratorService


router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/jobs")
async def list_jobs(
    config: AppConfig = Depends(get_config),
) -> dict[str, list[dict[str, Any]]]:
    service = JobsService(config)
    return {"jobs": service.list_jobs()}


@router.get("/api/jobs/{job_id}/thumbnail")
async def get_job_thumbnail(
    job_id: str,
    config: AppConfig = Depends(get_config),
) -> FileResponse:
    service = JobsService(config)
    thumbnail_path = service.get_thumbnail_path(job_id)

    if thumbnail_path is None:
        raise HTTPException(status_code=404, detail="Thumbnail not found.")
    if not thumbnail_path.exists() or not thumbnail_path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found.")

    return FileResponse(path=thumbnail_path)


@router.get("/api/jobs/{job_id}/lecture")
async def get_job_lecture(
    job_id: str,
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    service = LectureService(config)
    lecture = service.get_lecture_payload(job_id)
    if lecture is None:
        raise HTTPException(status_code=404, detail="Job lecture not found.")
    return lecture


@router.get("/api/jobs/{job_id}/slides/{slide_name}")
async def get_job_slide_image(
    job_id: str,
    slide_name: str,
    config: AppConfig = Depends(get_config),
) -> FileResponse:
    service = LectureService(config)
    slide_path = service.get_slide_image_path(job_id, slide_name)
    if slide_path is None:
        raise HTTPException(status_code=404, detail="Slide image not found.")
    return FileResponse(path=slide_path)


@router.get("/api/jobs/{job_id}/slides/{slide_name}/rendered")
async def get_job_slide_rendered(
    job_id: str,
    slide_name: str,
    step_index: int = Query(0, ge=0),
    strength: float = Query(1.0, ge=0.0, le=2.0),
    config: AppConfig = Depends(get_config),
) -> Response:
    service = LectureService(config)
    data = service.render_step_text_recolor_image(
        job_id=job_id,
        slide_name=slide_name,
        step_index=step_index,
        strength=strength,
    )
    if data is None:
        raise HTTPException(status_code=404, detail="Rendered slide not found.")
    return Response(content=data, media_type="image/jpeg")


@router.get("/api/jobs/{job_id}/rendered/{image_name}/{filename}")
async def get_job_precomputed_rendered_image(
    job_id: str,
    image_name: str,
    filename: str,
    config: AppConfig = Depends(get_config),
) -> FileResponse:
    service = LectureService(config)
    rendered_path = service.get_precomputed_rendered_image_path(job_id, image_name, filename)
    if rendered_path is None:
        raise HTTPException(status_code=404, detail="Rendered image not found.")
    return FileResponse(path=rendered_path, media_type="image/jpeg")


@router.get("/api/jobs/{job_id}/input-pdf")
async def get_job_input_pdf(
    job_id: str,
    config: AppConfig = Depends(get_config),
) -> FileResponse:
    service = LectureService(config)
    pdf_path = service.get_input_pdf_path(job_id)
    if pdf_path is None:
        raise HTTPException(status_code=404, detail="Input PDF not found.")
    return FileResponse(path=pdf_path, filename=pdf_path.name, media_type="application/pdf")


@router.get("/api/jobs/{job_id}/audio")
async def get_job_audio(
    job_id: str,
    config: AppConfig = Depends(get_config),
) -> FileResponse:
    service = LectureService(config)
    audio_path = service.get_transcript_audio_path(job_id)
    if audio_path is None:
        raise HTTPException(status_code=404, detail="Audio not found.")

    media_type = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
    }.get(audio_path.suffix.lower(), "application/octet-stream")

    return FileResponse(path=audio_path, filename=audio_path.name, media_type=media_type)


@router.post("/api/process-pdf")
async def process_pdf(
    pdf: UploadFile = File(...),
    method: str = Query("pelt", pattern="^(pelt|window|binseg)$"),
    penalty: Optional[float] = Query(None, description="Compatibility-only; ignored in current upstream flow."),
    n_bkps: Optional[int] = Query(
        None,
        ge=1,
        description="Compatibility-only; ignored in current upstream flow.",
    ),
    min_chunk: int = Query(2, ge=1),
    use_embeddings: bool = Query(True),
    use_cache: bool = Query(True),
    skip_generation: bool = Query(False, description="Forwarded to NeuroNote API."),
    previous_context: Optional[str] = Query(
        None,
        description="Compatibility-only; ignored in current upstream flow.",
    ),
    render_dpi: Optional[int] = Query(None, ge=72, le=600),
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    filename = (pdf.filename or "").lower()
    if not filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported.")

    pdf_bytes = await pdf.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty.")
    max_pdf_bytes = config.max_pdf_size_mb * 1024 * 1024
    if len(pdf_bytes) > max_pdf_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"PDF exceeds size limit of {config.max_pdf_size_mb} MB.",
        )

    options = ProcessPdfOptions(
        method=method,
        penalty=penalty,
        n_bkps=n_bkps,
        min_chunk=min_chunk,
        use_embeddings=use_embeddings,
        use_cache=use_cache,
        skip_generation=skip_generation,
        previous_context=previous_context,
        render_dpi=render_dpi or config.default_render_dpi,
    )

    orchestrator = OrchestratorService(config)

    try:
        return await orchestrator.process_pdf(
            pdf_bytes=pdf_bytes,
            options=options,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

```

## FILE: transcript_to_audio.py
```python
#!/usr/bin/env python3
"""Convert lecture transcript text to audio using OpenAI gpt-4o-mini-tts.

Usage examples:
  python3 transcript_to_audio.py
  python3 transcript_to_audio.py --job-id c3fcffaf1511 --voice alloy
  python3 transcript_to_audio.py --transcript-file ./transcript.txt --output ./transcript_audio.wav
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "gpt-4o-mini-tts"
DEFAULT_VOICE = "marin"
DEFAULT_MAX_CHARS = 3500
OPENAI_AUDIO_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
DEFAULT_TTS_INSTRUCTIONS = (
    "Speak as a patient university instructor.\n"
    "Explain concepts clearly and methodically.\n"
    "Use short pauses between ideas.\n"
    "Avoid dramatic emphasis."
)
SLIDE_INDEX_RE = re.compile(r"(?:^|_)page_(\d+)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--job-id",
        default=None,
        help="Job ID from jobs/<job_id>. If omitted, the newest local job is used.",
    )
    parser.add_argument(
        "--jobs-root",
        default="jobs",
        help="Path to jobs directory (default: ./jobs).",
    )
    parser.add_argument(
        "--neuronote-pipeline-root",
        default=os.getenv("NEURONOTE_PIPELINE_ROOT", "/home/javad/NeuroNote_Pipeline"),
        help="Root path used to resolve /output/... script artifacts.",
    )
    parser.add_argument(
        "--transcript-file",
        default=None,
        help="Optional plaintext transcript file. If set, job extraction is skipped.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output WAV path. Defaults to jobs/<job_id>/transcript_audio.wav.",
    )
    parser.add_argument(
        "--timestamps-file",
        default=None,
        help=(
            "Optional output JSON path for per-step timing metadata. "
            "Default: jobs/<job_id>/transcript_audio_timestamps.json"
        ),
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"TTS model (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--voice",
        default=DEFAULT_VOICE,
        help=f"TTS voice (default: {DEFAULT_VOICE}).",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"Max characters per TTS request chunk (default: {DEFAULT_MAX_CHARS}).",
    )
    parser.add_argument(
        "--no-slide-headings",
        action="store_true",
        help="Do not insert 'Slide N.' headings in the saved transcript text file.",
    )
    parser.add_argument(
        "--instructions",
        default=os.getenv("TRANSCRIPT_TTS_INSTRUCTIONS", DEFAULT_TTS_INSTRUCTIONS),
        help=(
            "Style instructions forwarded to the TTS model. "
            "Default uses a patient university instructor style."
        ),
    )
    return parser.parse_args()


def newest_job_id(jobs_root: Path) -> str:
    candidates: list[tuple[float, str]] = []
    for entry in jobs_root.iterdir():
        if not entry.is_dir():
            continue
        result_path = entry / "result.json"
        ts = result_path.stat().st_mtime if result_path.exists() else entry.stat().st_mtime
        candidates.append((ts, entry.name))
    if not candidates:
        raise RuntimeError(f"No jobs found in {jobs_root}")
    candidates.sort(reverse=True)
    return candidates[0][1]


def resolve_job_dir(jobs_root: Path, job_id: str) -> Path:
    job_dir = (jobs_root / job_id).resolve()
    if not job_dir.exists() or not job_dir.is_dir():
        raise FileNotFoundError(f"Job directory not found: {job_dir}")
    return job_dir


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object at {path}")
    return payload


def parse_artifact_roots(raw: str) -> list[Path]:
    if not raw:
        return []

    tokens: list[str] = []
    for part in raw.split(","):
        tokens.extend(part.split(os.pathsep))

    roots: list[Path] = []
    seen: set[str] = set()
    for token in tokens:
        item = token.strip()
        if not item:
            continue
        try:
            resolved = Path(item).expanduser().resolve()
        except Exception:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


def resolve_output_roots(
    neuronote_pipeline_root: Path,
    extra_roots: list[Path] | None = None,
) -> list[Path]:
    candidates: list[Path] = [
        neuronote_pipeline_root / "neuronote" / "jobs",
        neuronote_pipeline_root / "jobs",
        neuronote_pipeline_root,
        Path.home() / "NeuroPresentsBackend" / "neuropresentsbackend" / "jobs",
    ]
    if extra_roots:
        candidates.extend(extra_roots)

    roots: list[Path] = []
    seen: set[str] = set()
    for root in candidates:
        try:
            resolved = root.resolve()
        except Exception:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


def parse_slide_number(*values: Any) -> int | None:
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        stem = Path(value).stem
        match = SLIDE_INDEX_RE.search(stem)
        if not match:
            continue
        try:
            return int(match.group(1))
        except Exception:
            continue
    return None


def resolve_artifact_path(artifact_url: str, output_roots: list[Path]) -> Path | None:
    if not artifact_url or not artifact_url.startswith("/"):
        return None
    rel = artifact_url.lstrip("/")
    if ".." in rel.split("/"):
        return None

    for root in output_roots:
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root)
        except Exception:
            continue
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def extract_step_items_from_job(
    job_dir: Path,
    neuronote_pipeline_root: Path,
    artifact_roots: list[Path] | None = None,
) -> list[dict[str, Any]]:
    result_path = job_dir / "result.json"
    if not result_path.exists():
        raise FileNotFoundError(f"Missing result.json in job: {job_dir}")

    payload = read_json(result_path)
    chunks = payload.get("neuronote_chunks")
    if not isinstance(chunks, list):
        raise RuntimeError("result.json has no neuronote_chunks list")

    output_roots = resolve_output_roots(neuronote_pipeline_root, artifact_roots)
    items: list[dict[str, Any]] = []

    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        neuronote = chunk.get("neuronote")
        if not isinstance(neuronote, dict):
            continue
        result = neuronote.get("result")
        if not isinstance(result, dict):
            continue
        images = result.get("images")
        if not isinstance(images, list):
            continue

        for image in images:
            if not isinstance(image, dict):
                continue
            image_name = image.get("image_name")
            object_path = image.get("object_path")
            script_url = image.get("script_url")
            if not isinstance(script_url, str):
                continue

            slide_number = parse_slide_number(object_path, image_name)
            if slide_number is None:
                continue
            normalized_image_name = (
                Path(object_path).stem
                if isinstance(object_path, str) and object_path.strip()
                else str(image_name or f"page_{slide_number:03d}")
            )

            script_path = resolve_artifact_path(script_url, output_roots)
            if script_path is None:
                continue

            script_payload = read_json(script_path)
            steps = script_payload.get("steps")
            if not isinstance(steps, list):
                continue

            for raw_idx, step in enumerate(steps, start=1):
                if not isinstance(step, dict):
                    continue
                line = step.get("line")
                if not isinstance(line, str):
                    continue
                text = line.strip()
                if not text:
                    continue

                items.append(
                    {
                        "slide_number": slide_number,
                        "image_name": normalized_image_name,
                        "step_number": raw_idx,
                        "step_id": str(step.get("step_id") or f"s{raw_idx}"),
                        "line": text,
                    }
                )

    if not items:
        raise RuntimeError("No transcript lines found in job scripts")

    items.sort(key=lambda x: (int(x["slide_number"]), int(x["step_number"])))
    return items


def build_transcript_text(step_items: list[dict[str, Any]], include_slide_headings: bool) -> str:
    parts: list[str] = []
    current_slide = -1
    for item in step_items:
        slide_number = int(item["slide_number"])
        line = str(item["line"])
        if include_slide_headings and slide_number != current_slide:
            parts.append(f"Slide {slide_number}.")
            current_slide = slide_number
        parts.append(line)
    return "\n".join(parts)


def split_text(text: str, max_chars: int) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        if len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(sentence), max_chars):
                chunks.append(sentence[i : i + max_chars])
            continue

        tentative = sentence if not current else f"{current} {sentence}"
        if len(tentative) <= max_chars:
            current = tentative
        else:
            chunks.append(current)
            current = sentence

    if current:
        chunks.append(current)
    return chunks


def synthesize_wav_chunk(
    *,
    api_key: str,
    model: str,
    voice: str,
    text: str,
    instructions: str | None = None,
    timeout_seconds: float = 180.0,
) -> bytes:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "voice": voice,
        "input": text,
        "response_format": "wav",
    }
    if instructions and instructions.strip():
        payload["instructions"] = instructions.strip()
    req = urllib.request.Request(
        OPENAI_AUDIO_SPEECH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            status = int(resp.getcode() or 0)
            body = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        # Backward compatibility: retry once without instructions for models/endpoints
        # that do not support this field.
        if instructions and "instructions" in detail.lower():
            fallback_payload = dict(payload)
            fallback_payload.pop("instructions", None)
            fallback_req = urllib.request.Request(
                OPENAI_AUDIO_SPEECH_URL,
                data=json.dumps(fallback_payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(fallback_req, timeout=timeout_seconds) as resp:
                    status = int(resp.getcode() or 0)
                    body = resp.read()
                if status < 400:
                    return body
            except Exception:
                pass
        raise RuntimeError(f"TTS request failed ({exc.code}): {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"TTS request failed: {exc}") from exc

    if status >= 400:
        raise RuntimeError(f"TTS request failed ({status})")
    return body


def read_wav_frames(wav_bytes: bytes) -> tuple[tuple[int, int, int], bytes, int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as src:
        params = (src.getnchannels(), src.getsampwidth(), src.getframerate())
        nframes = src.getnframes()
        frames = src.readframes(nframes)
    return params, frames, nframes


def merge_wav_chunks_to_bytes(wav_chunks: list[bytes]) -> bytes:
    if not wav_chunks:
        raise RuntimeError("No WAV chunks to merge")

    base_params: tuple[int, int, int] | None = None
    all_frames: list[bytes] = []

    for index, wav_bytes in enumerate(wav_chunks, start=1):
        params, frames, _ = read_wav_frames(wav_bytes)
        if base_params is None:
            base_params = params
        elif params != base_params:
            raise RuntimeError(
                f"WAV mismatch at chunk {index}: got {params}, expected {base_params}"
            )
        all_frames.append(frames)

    assert base_params is not None
    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        dst.setnchannels(base_params[0])
        dst.setsampwidth(base_params[1])
        dst.setframerate(base_params[2])
        for frames in all_frames:
            dst.writeframes(frames)
    return out.getvalue()


def wav_duration_ms(wav_bytes: bytes) -> int:
    params, _, nframes = read_wav_frames(wav_bytes)
    framerate = params[2]
    if framerate <= 0:
        return 0
    return int(round((nframes * 1000) / framerate))


def write_wav_file(wav_bytes: bytes, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(wav_bytes)


def synthesize_text_to_wav(
    *,
    api_key: str,
    model: str,
    voice: str,
    text: str,
    max_chars: int,
    instructions: str | None = None,
) -> tuple[bytes, int]:
    chunks = split_text(text, max_chars)
    if not chunks:
        raise RuntimeError("Text is empty after normalization")

    wav_chunks: list[bytes] = []
    for chunk in chunks:
        wav_chunks.append(
            synthesize_wav_chunk(
                api_key=api_key,
                model=model,
                voice=voice,
                text=chunk,
                instructions=instructions,
            )
        )

    merged = merge_wav_chunks_to_bytes(wav_chunks)
    return merged, len(chunks)


def resolve_output_path(args: argparse.Namespace, job_dir: Path | None) -> Path:
    if args.output:
        return Path(args.output).expanduser().resolve()
    if job_dir is not None:
        return (job_dir / "transcript_audio.wav").resolve()
    return Path("transcript_audio.wav").resolve()


def resolve_timestamps_path(args: argparse.Namespace, job_dir: Path | None) -> Path | None:
    if args.timestamps_file:
        return Path(args.timestamps_file).expanduser().resolve()
    if job_dir is not None:
        return (job_dir / "transcript_audio_timestamps.json").resolve()
    return None


def load_dotenv_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, raw_value = line.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            continue

        value = raw_value
        if raw_value and raw_value[0] in {"'", '"'}:
            quote = raw_value[0]
            end = 1
            while end < len(raw_value):
                if raw_value[end] == quote and raw_value[end - 1] != "\\":
                    break
                end += 1
            if end < len(raw_value):
                value = raw_value[1:end]
            else:
                value = raw_value[1:]
        else:
            value = raw_value.split(" #", 1)[0].strip()

        os.environ.setdefault(key, value)


def generate_job_audio(
    *,
    job_id: str,
    jobs_root: Path,
    neuronote_pipeline_root: Path,
    model: str = DEFAULT_MODEL,
    voice: str = DEFAULT_VOICE,
    max_chars: int = DEFAULT_MAX_CHARS,
    include_slide_headings: bool = True,
    instructions: str = DEFAULT_TTS_INSTRUCTIONS,
    api_key: str | None = None,
    output_path: Path | None = None,
    timestamps_path: Path | None = None,
    artifact_roots: list[Path] | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    if max_chars < 500:
        raise ValueError("max_chars must be at least 500")

    jobs_root = Path(jobs_root).expanduser().resolve()
    neuronote_pipeline_root = Path(neuronote_pipeline_root).expanduser().resolve()
    job_dir = resolve_job_dir(jobs_root, job_id)

    step_items = extract_step_items_from_job(
        job_dir=job_dir,
        neuronote_pipeline_root=neuronote_pipeline_root,
        artifact_roots=artifact_roots,
    )
    transcript_text = build_transcript_text(
        step_items,
        include_slide_headings=include_slide_headings,
    )
    (job_dir / "transcript.txt").write_text(transcript_text)

    def log(message: str) -> None:
        if verbose:
            print(message)

    all_step_wavs: list[bytes] = []
    timing_steps: list[dict[str, Any]] = []
    current_ms = 0
    total_chunks = 0

    for idx, item in enumerate(step_items, start=1):
        line = str(item["line"])
        log(
            "Synthesizing step "
            f"{idx}/{len(step_items)} "
            f"(slide {item['slide_number']} step {item['step_number']})"
        )

        step_wav, chunk_count = synthesize_text_to_wav(
            api_key=api_key,
            model=model,
            voice=voice,
            text=line,
            max_chars=max_chars,
            instructions=instructions,
        )
        total_chunks += chunk_count

        duration_ms = wav_duration_ms(step_wav)
        start_ms = current_ms
        end_ms = start_ms + duration_ms
        current_ms = end_ms

        timing_steps.append(
            {
                "slide_number": int(item["slide_number"]),
                "image_name": str(item["image_name"]),
                "step_number": int(item["step_number"]),
                "step_id": str(item["step_id"]),
                "line": line,
                "audio_start_ms": start_ms,
                "audio_end_ms": end_ms,
            }
        )
        all_step_wavs.append(step_wav)

    merged_wav = merge_wav_chunks_to_bytes(all_step_wavs)
    resolved_output_path = (output_path or (job_dir / "transcript_audio.wav")).expanduser().resolve()
    write_wav_file(merged_wav, resolved_output_path)

    resolved_timestamps_path: Path | None
    if timestamps_path is None:
        resolved_timestamps_path = (job_dir / "transcript_audio_timestamps.json").resolve()
    else:
        resolved_timestamps_path = timestamps_path.expanduser().resolve()

    if resolved_timestamps_path is not None:
        resolved_timestamps_path.parent.mkdir(parents=True, exist_ok=True)
        timestamps_payload = {
            "job_id": job_id,
            "audio_file": resolved_output_path.name,
            "model": model,
            "voice": voice,
            "instructions": instructions,
            "steps": timing_steps,
            "total_duration_ms": current_ms,
        }
        resolved_timestamps_path.write_text(json.dumps(timestamps_payload, indent=2))
        log(f"Timestamps written to: {resolved_timestamps_path}")

    log(f"Audio written to: {resolved_output_path}")
    log(f"Steps: {len(step_items)}")
    log(f"Chunks: {total_chunks}")
    log(f"Characters: {len(' '.join(transcript_text.split()))}")

    return {
        "job_id": job_id,
        "audio_path": str(resolved_output_path),
        "timestamps_path": str(resolved_timestamps_path) if resolved_timestamps_path is not None else None,
        "steps": len(step_items),
        "chunks": total_chunks,
        "characters": len(" ".join(transcript_text.split())),
        "total_duration_ms": current_ms,
        "model": model,
        "voice": voice,
    }


def main() -> int:
    load_dotenv_file(Path(".env"))
    args = parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    if args.max_chars < 500:
        raise ValueError("--max-chars must be at least 500")

    jobs_root = Path(args.jobs_root).expanduser().resolve()
    pipeline_root = Path(args.neuronote_pipeline_root).expanduser().resolve()

    if args.transcript_file:
        transcript_path = Path(args.transcript_file).expanduser().resolve()
        transcript_text = transcript_path.read_text().strip()
        if not transcript_text:
            raise RuntimeError(f"Transcript file is empty: {transcript_path}")

        print("Synthesizing transcript file...")
        merged_wav, chunk_count = synthesize_text_to_wav(
            api_key=api_key,
            model=args.model,
            voice=args.voice,
            text=transcript_text,
            max_chars=args.max_chars,
            instructions=args.instructions,
        )

        output_path = resolve_output_path(args, None)
        write_wav_file(merged_wav, output_path)

        print(f"Audio written to: {output_path}")
        print(f"Chunks: {chunk_count}")
        print(f"Characters: {len(' '.join(transcript_text.split()))}")
        return 0

    if not jobs_root.exists():
        raise FileNotFoundError(f"Jobs root not found: {jobs_root}")

    job_id = args.job_id or newest_job_id(jobs_root)

    artifact_roots = parse_artifact_roots(os.getenv("NEURONOTE_ARTIFACT_ROOTS", ""))
    generate_job_audio(
        job_id=job_id,
        jobs_root=jobs_root,
        neuronote_pipeline_root=pipeline_root,
        model=args.model,
        voice=args.voice,
        max_chars=args.max_chars,
        include_slide_headings=not args.no_slide_headings,
        instructions=args.instructions,
        api_key=api_key,
        output_path=Path(args.output).expanduser().resolve() if args.output else None,
        timestamps_path=(
            Path(args.timestamps_file).expanduser().resolve()
            if args.timestamps_file
            else None
        ),
        artifact_roots=artifact_roots,
        verbose=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

```

## FILE: /home/javad/NeuroNote/chunking_slides_v2/src/ocr.py
```python
"""OCR module using PaddleOCR for text extraction from slide images."""

import logging
from pathlib import Path
from typing import List, Tuple, Optional

from PIL import Image
import numpy as np

logger = logging.getLogger(__name__)

# Lazy load PaddleOCR to avoid slow import at module level
_ocr_instance = None


def get_ocr():
    """Get or create PaddleOCR instance (singleton)."""
    global _ocr_instance
    if _ocr_instance is None:
        from paddleocr import PaddleOCR
        _ocr_instance = PaddleOCR(
            use_angle_cls=True,
            lang='en'        )
    return _ocr_instance


def extract_text_from_image(image_path: Path) -> str:
    """
    Extract text from an image using PaddleOCR.
    
    Args:
        image_path: Path to the image file
        
    Returns:
        Extracted text in reading order (top-to-bottom, left-to-right)
    """
    ocr = get_ocr()
    
    # Run OCR
    result = ocr.ocr(str(image_path))
    
    if not result:
        logger.debug(f"No text found in {image_path}")
        return ""
    
    # Handle different result formats from different PaddleOCR versions
    # New format: list of dicts with 'rec_texts', 'rec_scores', 'dt_polys'
    # Old format: list of [bbox, (text, confidence)]
    
    text_boxes = []
    
    # Check if result is the new dict format
    if isinstance(result, dict):
        # New PaddleOCR format
        texts = result.get('rec_texts', [])
        polys = result.get('dt_polys', [])
        scores = result.get('rec_scores', [])
        
        for i, text in enumerate(texts):
            if not text.strip():
                continue
            
            # Get polygon points if available
            if i < len(polys) and len(polys[i]) >= 4:
                poly = polys[i]
                center_y = sum(p[1] for p in poly) / len(poly)
                center_x = sum(p[0] for p in poly) / len(poly)
            else:
                center_y = i * 10  # Fallback ordering
                center_x = 0
            
            confidence = scores[i] if i < len(scores) else 1.0
            
            text_boxes.append({
                'text': text,
                'center_y': center_y,
                'center_x': center_x,
                'confidence': confidence
            })
    elif isinstance(result, list):
        # Could be old format or list of dicts
        for item in result:
            if item is None:
                continue
            
            # If item is a dict (new per-image format)
            if isinstance(item, dict):
                texts = item.get('rec_texts', [])
                polys = item.get('dt_polys', [])
                scores = item.get('rec_scores', [])
                
                for i, text in enumerate(texts):
                    if not text.strip():
                        continue
                    
                    if i < len(polys) and len(polys[i]) >= 4:
                        poly = polys[i]
                        center_y = sum(p[1] for p in poly) / len(poly)
                        center_x = sum(p[0] for p in poly) / len(poly)
                    else:
                        center_y = i * 10
                        center_x = 0
                    
                    confidence = scores[i] if i < len(scores) else 1.0
                    
                    text_boxes.append({
                        'text': text,
                        'center_y': center_y,
                        'center_x': center_x,
                        'confidence': confidence
                    })
            # Old format: list of [bbox, (text, confidence)] or [bbox, text]
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                bbox = item[0]
                text_info = item[1]
                
                # Extract text - could be (text, conf) tuple or just text string
                if isinstance(text_info, (list, tuple)) and len(text_info) >= 1:
                    text = str(text_info[0])
                    confidence = float(text_info[1]) if len(text_info) > 1 else 1.0
                else:
                    text = str(text_info)
                    confidence = 1.0
                
                if not text.strip():
                    continue
                
                # Calculate center from bbox
                if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                    try:
                        center_y = sum(float(point[1]) for point in bbox) / len(bbox)
                        center_x = sum(float(point[0]) for point in bbox) / len(bbox)
                    except (TypeError, IndexError):
                        center_y = 0
                        center_x = 0
                else:
                    center_y = 0
                    center_x = 0
                
                text_boxes.append({
                    'text': text,
                    'center_y': center_y,
                    'center_x': center_x,
                    'confidence': confidence
                })
    
    if not text_boxes:
        logger.debug(f"No text extracted from {image_path}")
        return ""
    
    # Sort by y position (top to bottom), then x (left to right)
    text_boxes.sort(key=lambda x: (x['center_y'], x['center_x']))
    
    # Group text into lines (boxes with similar y are on same line)
    lines = []
    current_line = []
    last_y = None
    y_threshold = 20  # Pixels threshold for same line
    
    for box in text_boxes:
        if last_y is None or abs(box['center_y'] - last_y) < y_threshold:
            current_line.append(box)
        else:
            if current_line:
                # Sort current line by x position
                current_line.sort(key=lambda x: x['center_x'])
                lines.append(' '.join(b['text'] for b in current_line))
            current_line = [box]
        last_y = box['center_y']
    
    # Don't forget the last line
    if current_line:
        current_line.sort(key=lambda x: x['center_x'])
        lines.append(' '.join(b['text'] for b in current_line))
    
    return '\n'.join(lines)


def extract_text_from_region(
    image_path: Path,
    bbox: Tuple[float, float, float, float],
    image_size: Optional[Tuple[int, int]] = None
) -> str:
    """
    Extract text from a specific region of an image.
    
    Args:
        image_path: Path to the image file
        bbox: Bounding box as (x1, y1, x2, y2) in normalized coordinates (0-1)
        image_size: Optional (width, height) if already known
        
    Returns:
        Extracted text from the region
    """
    img = Image.open(image_path)
    width, height = img.size
    
    # Convert normalized coords to pixel coords
    x1 = int(bbox[0] * width)
    y1 = int(bbox[1] * height)
    x2 = int(bbox[2] * width)
    y2 = int(bbox[3] * height)
    
    # Crop the region
    cropped = img.crop((x1, y1, x2, y2))
    
    # Save to temp and run OCR
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        temp_path = Path(f.name)
        cropped.save(temp_path)
    
    try:
        text = extract_text_from_image(temp_path)
    finally:
        temp_path.unlink()
    
    return text


def batch_extract_text(image_paths: List[Path]) -> List[str]:
    """
    Extract text from multiple images.
    
    Args:
        image_paths: List of paths to image files
        
    Returns:
        List of extracted texts in the same order
    """
    results = []
    for path in image_paths:
        try:
            text = extract_text_from_image(path)
            results.append(text)
        except Exception as e:
            logger.error(f"Error extracting text from {path}: {e}")
            results.append("")
    return results


```

## FILE: /home/javad/NeuroNote/chunking_slides_v2/src/pipeline.py
```python
"""Main pipeline orchestrating all components."""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional, Any

import numpy as np
from tqdm import tqdm

from .bbox import parse_yolo_obb_label, BBox, sort_top_to_bottom
from .embeddings import embed_text, embed_texts_batch

# Lazy imports to avoid loading OCR/Vision when using cache
_ocr_module = None
_vision_module = None

def _get_ocr():
    global _ocr_module
    if _ocr_module is None:
        from . import ocr as _ocr_module
    return _ocr_module

def _get_vision():
    global _vision_module
    if _vision_module is None:
        from . import vision as _vision_module
    return _vision_module
from .chunking import (
    threshold_chunking,
    clustering_chunking, 
    centroid_chunking,
    topic_shift_chunking_kl,
    changepoint_similarity_chunking,
    switchvi_chunking_enhanced,
    entailment_chunking,
    Chunk,
    chunks_to_json,
    evaluate_chunking
)

logger = logging.getLogger(__name__)

# Cache directory
CACHE_DIR = Path(__file__).parent.parent / ".slide_cache"


@dataclass
class SlideContent:
    """Represents extracted content from a single slide."""
    slide_num: int
    image_path: Path
    ocr_text: str = ""
    bbox_descriptions: Dict[int, Dict[str, str]] = field(default_factory=dict)
    # {"brief": "...", "detail": "..."}
    combined_text: str = ""  # For embedding - uses OCR + brief descriptions
    embedding: Optional[np.ndarray] = None
    
    def combine_content(self):
        """Combine OCR text and brief bbox descriptions for embedding."""
        sections = []
        
        # OCR text section
        if self.ocr_text.strip():
            sections.append(f"[TEXT] {self.ocr_text.strip()}")
        
        # Visual descriptions - consolidated into one line
        visual_items = []
        if self.bbox_descriptions:
            for bbox_id in sorted(self.bbox_descriptions.keys()):
                desc_data = self.bbox_descriptions[bbox_id]
                # Handle both old format (str) and new format (dict)
                if isinstance(desc_data, str):
                    brief = desc_data
                else:
                    brief = desc_data.get("brief", "")
                # Skip empty, "None", or useless values
                brief = brief.strip()
                if brief and brief.lower() not in ("none", "n/a", "empty"):
                    visual_items.append(brief)
        
        # Always include visual line
        if visual_items:
            sections.append(f"[VISUAL] Key elements: {'; '.join(visual_items)}.")
        else:
            sections.append("[VISUAL] Key elements: none.")
        
        self.combined_text = "\n".join(sections)


@dataclass
class ChunkingResult:
    """Results from a chunking method."""
    method_name: str
    chunks: List[Chunk]
    metrics: Optional[Dict[str, Any]] = None


class SlideChunkingPipeline:
    """Main pipeline for processing and chunking slides."""
    
    def __init__(
        self,
        slide_dir: Path,
        use_vision: bool = True,
        use_cache: bool = True,
        threshold_params: Optional[Dict] = None,
        clustering_params: Optional[Dict] = None,
        centroid_params: Optional[Dict] = None,
        changepoint_params: Optional[Dict] = None,
        topic_shift_params: Optional[Dict] = None,
        entailment_params: Optional[Dict] = None
    ):
        """
        Initialize the pipeline.
        
        Args:
            slide_dir: Path to slide directory (contains images/ and labels/ subdirs)
            use_vision: Whether to use GPT-4 Vision for bbox descriptions
            use_cache: Whether to use caching for OCR/embeddings
            threshold_params: Parameters for threshold chunking
            clustering_params: Parameters for clustering chunking
            centroid_params: Parameters for centroid chunking
            changepoint_params: Parameters for changepoint chunking
            topic_shift_params: Parameters for topic shift chunking
        """
        self.slide_dir = Path(slide_dir)
        self.images_dir = self.slide_dir / "images"
        self.labels_dir = self.slide_dir / "labels"
        self.use_vision = use_vision
        self.use_cache = use_cache
        
        # Default parameters - tuned for better chunk detection
        self.threshold_params = threshold_params or {'threshold': 0.65}
        self.clustering_params = clustering_params or {'distance_threshold': 0.4, 'method': 'ward'}
        self.centroid_params = centroid_params or {'distance_threshold': 0.3, 'consecutive_count': 1}
        self.changepoint_params = changepoint_params or {'method': 'pelt', 'penalty': 0.5, 'min_chunk': 1, 'use_embeddings': True}
        self.topic_shift_params = topic_shift_params or {'kl_threshold': 0.35, 'window_size': 2, 'n_topics': 12, 'temperature': 0.05}
        self.switchvi_params = {'window_size': 4, 'js_threshold': 0.005, 'temperature': 0.08, 'gaussian_sigma': 0.5}
        self.entailment_params = entailment_params or {
            'use_hybrid': True,
            'high_threshold': 0.75,
            'low_threshold': 0.60,
            'nli_threshold': 0.40,
            'entailment_threshold': 0.65,  # fallback for non-hybrid
            'model_name': 'cross-encoder/nli-deberta-v3-large'
        }
        
        # Results storage
        self.slide_contents: List[SlideContent] = []
        self.chunking_results: Dict[str, ChunkingResult] = {}
        
        # Setup cache
        self.cache_dir = CACHE_DIR / self.slide_dir.name
        if use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_cache_path(self, slide_num: int) -> Path:
        """Get cache file path for a slide."""
        return self.cache_dir / f"slide_{slide_num:03d}.json"
    
    def _load_from_cache(self, slide_num: int) -> Optional[Dict]:
        """Load slide content from cache if available."""
        cache_path = self._get_cache_path(slide_num)
        if cache_path.exists():
            try:
                with open(cache_path) as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load cache for slide {slide_num}: {e}")
        return None
    
    def _save_to_cache(self, slide_num: int, data: Dict):
        """Save slide content to cache."""
        cache_path = self._get_cache_path(slide_num)
        try:
            with open(cache_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save cache for slide {slide_num}: {e}")
    
    def _get_content_hash(self) -> str:
        """Get hash of all combined texts to detect content changes."""
        texts = "||".join(s.combined_text for s in self.slide_contents)
        return hashlib.md5(texts.encode()).hexdigest()[:16]
    
    def _load_embeddings_cache(self) -> Optional[List[np.ndarray]]:
        """Load cached embeddings for all slides."""
        emb_path = self.cache_dir / "embeddings.npy"
        hash_path = self.cache_dir / "embeddings_hash.txt"
        
        if emb_path.exists() and hash_path.exists():
            try:
                # Check if content hash matches
                cached_hash = hash_path.read_text().strip()
                current_hash = self._get_content_hash()
                if cached_hash != current_hash:
                    logger.info("Content changed, will recompute embeddings")
                    return None
                return list(np.load(emb_path, allow_pickle=True))
            except Exception as e:
                logger.warning(f"Failed to load embeddings cache: {e}")
        return None
    
    def _save_embeddings_cache(self, embeddings: List[np.ndarray]):
        """Save embeddings to cache."""
        emb_path = self.cache_dir / "embeddings.npy"
        hash_path = self.cache_dir / "embeddings_hash.txt"
        try:
            np.save(emb_path, np.array(embeddings, dtype=object), allow_pickle=True)
            hash_path.write_text(self._get_content_hash())
        except Exception as e:
            logger.warning(f"Failed to save embeddings cache: {e}")
    
    def get_slide_images(self) -> List[Path]:
        """Get sorted list of slide image paths."""
        images = sorted(self.images_dir.glob("page_*.png"))
        return images
    
    def get_label_file(self, slide_num: int) -> Optional[Path]:
        """Get label file for a slide if it exists."""
        label_path = self.labels_dir / f"page_{slide_num:03d}.txt"
        if label_path.exists():
            return label_path
        return None
    
    async def process_slide(self, image_path: Path) -> SlideContent:
        """
        Process a single slide: OCR + bbox vision processing.
        Uses cache if available.
        
        Args:
            image_path: Path to the slide image
            
        Returns:
            SlideContent with all extracted information
        """
        # Parse slide number from filename
        slide_num = int(image_path.stem.split('_')[1])
        
        # Try to load from cache
        if self.use_cache:
            cached = self._load_from_cache(slide_num)
            if cached:
                content = SlideContent(
                    slide_num=slide_num,
                    image_path=image_path,
                    ocr_text=cached.get('ocr_text', ''),
                    bbox_descriptions=cached.get('bbox_descriptions', {}),
                    combined_text=cached.get('combined_text', '')
                )
                # Convert bbox_descriptions keys back to int
                content.bbox_descriptions = {
                    int(k): v for k, v in content.bbox_descriptions.items()
                }
                logger.debug(f"Loaded slide {slide_num} from cache")
                return content
        
        content = SlideContent(
            slide_num=slide_num,
            image_path=image_path
        )
        
        # Extract OCR text (lazy load OCR module)
        content.ocr_text = _get_ocr().extract_text_from_image(image_path)
        
        # Process bboxes if they exist
        label_file = self.get_label_file(slide_num)
        if label_file and self.use_vision:
            bboxes = parse_yolo_obb_label(label_file)
            if bboxes:
                content.bbox_descriptions = await _get_vision().process_bboxes_hierarchical(
                    image_path, bboxes
                )
        
        # Combine content
        content.combine_content()
        
        # Save to cache
        if self.use_cache:
            self._save_to_cache(slide_num, {
                'ocr_text': content.ocr_text,
                'bbox_descriptions': {str(k): v for k, v in content.bbox_descriptions.items()},
                'combined_text': content.combined_text
            })
        
        return content
    
    async def process_all_slides(self, show_progress: bool = True):
        """Process all slides in the directory."""
        images = self.get_slide_images()
        
        if not images:
            logger.warning(f"No slide images found in {self.images_dir}")
            return
        
        logger.info(f"Processing {len(images)} slides from {self.slide_dir.name}")
        
        # Process slides sequentially to manage API rate limits
        # (Vision API calls within each slide are parallel)
        iterator = tqdm(images, desc="Processing slides") if show_progress else images
        
        for image_path in iterator:
            content = await self.process_slide(image_path)
            self.slide_contents.append(content)
        
        # Sort by slide number
        self.slide_contents.sort(key=lambda x: x.slide_num)
    
    def compute_embeddings(self, show_progress: bool = True):
        """Compute embeddings for all slides. Uses cache if available."""
        # Try to load from cache first
        if self.use_cache:
            cached_embeddings = self._load_embeddings_cache()
            if cached_embeddings and len(cached_embeddings) == len(self.slide_contents):
                logger.info(f"Loaded {len(cached_embeddings)} embeddings from cache")
                for i, emb in enumerate(cached_embeddings):
                    self.slide_contents[i].embedding = emb
                return
        
        texts = [s.combined_text for s in self.slide_contents]
        
        logger.info(f"Computing embeddings for {len(texts)} slides")
        
        embeddings = embed_texts_batch(texts)
        
        for i, emb in enumerate(embeddings):
            self.slide_contents[i].embedding = emb
        
        # Save embeddings to cache
        if self.use_cache:
            self._save_embeddings_cache(embeddings)
    
    def run_chunking_methods(self):
        """Run all four chunking methods."""
        embeddings = [s.embedding for s in self.slide_contents]
        texts = [s.combined_text for s in self.slide_contents]
        
        if not embeddings or any(e is None for e in embeddings):
            logger.error("Embeddings not computed. Call compute_embeddings() first.")
            return
        
        # Threshold method
        threshold_chunks = threshold_chunking(embeddings, **self.threshold_params)
        self.chunking_results['threshold'] = ChunkingResult(
            method_name='threshold',
            chunks=threshold_chunks
        )
        logger.info(f"Threshold method: {len(threshold_chunks)} chunks")
        
        # Clustering method
        clustering_chunks = clustering_chunking(embeddings, **self.clustering_params)
        self.chunking_results['clustering'] = ChunkingResult(
            method_name='clustering',
            chunks=clustering_chunks
        )
        logger.info(f"Clustering method: {len(clustering_chunks)} chunks")
        
        # Centroid method
        centroid_chunks = centroid_chunking(embeddings, **self.centroid_params)
        self.chunking_results['centroid'] = ChunkingResult(
            method_name='centroid',
            chunks=centroid_chunks
        )
        logger.info(f"Centroid method: {len(centroid_chunks)} chunks")
        
        # Topic-shift method (BERTopic)
        topic_chunks = topic_shift_chunking_kl(embeddings, **self.topic_shift_params)
        self.chunking_results['topic_shift'] = ChunkingResult(
            method_name='topic_shift',
            chunks=topic_chunks
        )
        logger.info(f"Topic-shift method: {len(topic_chunks)} chunks")
        
        # Change-point detection on similarity signal (RBF kernel)
        cpd_chunks = changepoint_similarity_chunking(embeddings, **self.changepoint_params)
        self.chunking_results['changepoint'] = ChunkingResult(
            method_name='changepoint',
            chunks=cpd_chunks
        )
        logger.info(f"Change-point method: {len(cpd_chunks)} chunks")
        
        # SWITCHVI method (enhanced with background subtraction + gaussian weights)
        # switchvi_chunks = switchvi_chunking_enhanced(embeddings, **self.switchvi_params)
        # self.chunking_results['switchvi'] = ChunkingResult(
        #     method_name='switchvi',
        #     chunks=switchvi_chunks
        # )
        # logger.info(f"SWITCHVI method: {len(switchvi_chunks)} chunks")
        
        # Entailment-based method (NLI with embedding fallback)
        entailment_chunks = entailment_chunking(texts, embeddings=embeddings, **self.entailment_params)
        self.chunking_results['entailment'] = ChunkingResult(
            method_name='entailment',
            chunks=entailment_chunks
        )
        logger.info(f"Entailment method: {len(entailment_chunks)} chunks")
    
    def evaluate_against_ground_truth(self, ground_truth_path: Path):
        """
        Evaluate all chunking methods against ground truth.
        
        Args:
            ground_truth_path: Path to ground truth JSON file
        """
        with open(ground_truth_path) as f:
            ground_truth = json.load(f)
        
        for method_name, result in self.chunking_results.items():
            metrics = evaluate_chunking(result.chunks, ground_truth)
            result.metrics = metrics
            logger.info(f"{method_name} - F1: {metrics['f1']:.3f}, "
                       f"Precision: {metrics['precision']:.3f}, "
                       f"Recall: {metrics['recall']:.3f}, "
                       f"WinDiff: {metrics['windowdiff']:.3f}, "
                       f"Pk: {metrics['pk']:.3f}")
    
    def get_results(self) -> Dict[str, Any]:
        """Get all results as a dictionary."""
        results = {
            'slide_dir': str(self.slide_dir),
            'num_slides': len(self.slide_contents),
            'methods': {}
        }
        
        for method_name, result in self.chunking_results.items():
            results['methods'][method_name] = {
                'chunks': chunks_to_json(result.chunks),
                'metrics': result.metrics
            }
        
        # Add adjacent similarities for visualization
        embeddings = [s.embedding for s in self.slide_contents]
        if embeddings and all(e is not None for e in embeddings):
            from .embeddings import cosine_similarity
            similarities = []
            for i in range(len(embeddings) - 1):
                sim = cosine_similarity(embeddings[i], embeddings[i + 1])
                similarities.append(float(sim))
            results['similarities'] = similarities
        
        return results
    
    def save_results(self, output_dir: Path):
        """
        Save all results to output directory.
        
        Args:
            output_dir: Directory to save results
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Save combined results
        results = self.get_results()
        with open(output_dir / "results.json", 'w') as f:
            json.dump(results, f, indent=2)
        
        # Save individual method results
        for method_name, result in self.chunking_results.items():
            chunks_data = chunks_to_json(result.chunks)
            with open(output_dir / f"{method_name}_chunks.json", 'w') as f:
                json.dump(chunks_data, f, indent=2)
        
        # Save slide content summaries
        content_summary = []
        for slide in self.slide_contents:
            content_summary.append({
                'slide_num': slide.slide_num,
                'ocr_text_length': len(slide.ocr_text),
                'num_bbox_descriptions': len(slide.bbox_descriptions),
                'combined_text_preview': slide.combined_text[:200] + '...' if len(slide.combined_text) > 200 else slide.combined_text
            })
        
        with open(output_dir / "slide_contents.json", 'w') as f:
            json.dump(content_summary, f, indent=2)
        
        logger.info(f"Results saved to {output_dir}")


async def run_pipeline(
    slide_dir: Path,
    ground_truth_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    use_vision: bool = True,
    use_cache: bool = True,
    **kwargs
) -> Dict[str, Any]:
    """
    Run the full pipeline on a slide directory.
    
    Args:
        slide_dir: Path to slide directory
        ground_truth_path: Optional path to ground truth labels
        output_dir: Optional directory to save results
        use_vision: Whether to use GPT-4 Vision
        use_cache: Whether to use caching for OCR/embeddings
        **kwargs: Additional parameters for chunking methods
        
    Returns:
        Results dictionary
    """
    pipeline = SlideChunkingPipeline(slide_dir, use_vision=use_vision, use_cache=use_cache, **kwargs)
    
    # Process slides
    await pipeline.process_all_slides()
    
    # Compute embeddings
    pipeline.compute_embeddings()
    
    # Run chunking
    pipeline.run_chunking_methods()
    
    # Evaluate if ground truth provided
    if ground_truth_path:
        pipeline.evaluate_against_ground_truth(ground_truth_path)
    
    # Save results if output dir provided
    if output_dir:
        pipeline.save_results(output_dir)
    
    return pipeline.get_results()


```

## FILE: /home/javad/NeuroNote/chunking_slides_v2/src/chunking/changepoint.py
```python
"""Change-point detection-based chunking methods."""

import logging
from typing import List

import numpy as np

from .base import Chunk
from .threshold import threshold_chunking
from ..embeddings import cosine_similarity

logger = logging.getLogger(__name__)



def changepoint_similarity_chunking(
    embeddings: List[np.ndarray],
    method: str = "pelt",
    penalty: float = None,
    n_bkps: int = None,
    min_chunk: int = 2,
    use_embeddings: bool = True,
) -> List[Chunk]:
    """
    Chunk slides using change point detection.

    Can operate in two modes:
    - use_embeddings=True: Detect changes directly in embedding space (recommended)
    - use_embeddings=False: Detect changes in cosine distance signal

    Args:
        embeddings: List of slide embeddings
        method: CPD method: 'pelt' (automatic), 'binseg', or 'window'
        penalty: Penalty value for PELT (higher = fewer breakpoints). 
                 If None, auto-calculated.
        n_bkps: Fixed number of breakpoints (only for binseg/window).
        min_chunk: minimum allowed chunk length in slides
        use_embeddings: If True, run CPD on embeddings directly; else on distance signal

    Returns:
        List of Chunk objects
    """
    if not embeddings:
        return []

    n = len(embeddings)
    if n <= 2:
        return [Chunk(chunk_id=1, start_slide=1, end_slide=n)]

    try:
        import ruptures as rpt
    except ImportError:
        logger.warning(
            "ruptures is not installed; falling back to threshold_chunking"
        )
        return threshold_chunking(embeddings)

    # Build signal for CPD
    if use_embeddings:
        # Use embeddings directly - detect changes in embedding space
        # Convert to proper float array
        signal = np.array([np.asarray(e, dtype=np.float64) for e in embeddings])
        model = "rbf"  # RBF kernel works well for high-dim embeddings
    else:
        # Use cosine distance between adjacent slides
        sims = np.array([
            cosine_similarity(embeddings[i], embeddings[i + 1])
            for i in range(n - 1)
        ], dtype=float)
        signal = (1.0 - sims).reshape(-1, 1)  # (n-1, 1)
        model = "l2"

    T = len(signal)
    min_size = max(min_chunk, 1)

    logger.debug(f"CPD signal shape: {signal.shape}, model: {model}")

    # Auto-calculate penalty if not provided
    if penalty is None:
        if use_embeddings:
            # For high-dim embeddings, scale by dimension and variance
            penalty = np.log(n) * signal.var() * 10
        else:
            # For 1D distance signal
            penalty = signal.std() * 0.5
        penalty = max(penalty, 0.001)  # Ensure non-zero
        logger.info(f"Auto-calculated penalty: {penalty:.4f}")

    # Run CPD
    if method == "pelt":
        algo = rpt.Pelt(model=model, min_size=min_size, jump=1).fit(signal)
        break_points = algo.predict(pen=penalty)
    elif method == "window":
        width = max(3, min_size)
        algo = rpt.Window(width=width, model=model, min_size=min_size).fit(signal)
        break_points = algo.predict(n_bkps=n_bkps) if n_bkps else algo.predict(pen=penalty)
    else:  # binseg
        algo = rpt.Binseg(model=model, min_size=min_size).fit(signal)
        break_points = algo.predict(n_bkps=n_bkps) if n_bkps else algo.predict(pen=penalty)

    # Remove final endpoint (ruptures always includes len(signal))
    break_points = [bp for bp in break_points if bp < T]

    logger.info(f"CPD method={method}, penalty={penalty:.4f}, breakpoints: {break_points}")

    # Build chunks from breakpoints
    # breakpoints are indices where new chunk starts
    chunks: List[Chunk] = []
    chunk_id = 1
    start_slide = 1

    for bp in sorted(break_points):
        # bp is the index where new chunk starts (0-indexed)
        # So previous chunk ends at slide bp (1-indexed)
        end_slide = bp  # Convert to 1-indexed end
        if end_slide >= start_slide:
            chunks.append(Chunk(
                chunk_id=chunk_id,
                start_slide=start_slide,
                end_slide=end_slide,
            ))
            chunk_id += 1
            start_slide = end_slide + 1

    # Add final chunk
    if start_slide <= n:
        chunks.append(Chunk(
            chunk_id=chunk_id,
            start_slide=start_slide,
            end_slide=n,
        ))

    return chunks

```

## FILE: /home/javad/NeuroNote/chunking_slides_v2/src/chunking/base.py
```python
"""Base classes and utilities for chunking."""

from dataclasses import dataclass
from typing import List, Tuple, Dict, Set

import numpy as np


@dataclass
class Chunk:
    """Represents a chunk of slides."""
    chunk_id: int
    start_slide: int  # 1-indexed
    end_slide: int    # 1-indexed (inclusive)
    
    @property
    def slide_range(self) -> str:
        """Get slide range as string."""
        if self.start_slide == self.end_slide:
            return str(self.start_slide)
        return f"{self.start_slide}-{self.end_slide}"
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON output."""
        return {
            "chunk_id": self.chunk_id,
            "slide_range": self.slide_range
        }


def chunks_to_json(chunks: List[Chunk]) -> List[dict]:
    """Convert list of chunks to JSON-serializable format."""
    return [c.to_dict() for c in chunks]


def _compute_windowdiff(
    pred_breaks: Set[int], 
    gt_breaks: Set[int], 
    n_slides: int,
    k: int = None
) -> float:
    """
    Compute WindowDiff metric for segmentation evaluation.
    
    WindowDiff measures the difference between predicted and reference 
    segmentations by comparing the number of boundaries within a sliding 
    window. Lower is better (0 = perfect).
    
    Args:
        pred_breaks: Set of predicted break points (1-indexed slide numbers)
        gt_breaks: Set of ground truth break points (1-indexed slide numbers)
        n_slides: Total number of slides
        k: Window size. If None, uses half the average segment length.
        
    Returns:
        WindowDiff score (0.0 to 1.0, lower is better)
    """
    if n_slides <= 1:
        return 0.0
    
    # Convert break points to binary boundary arrays (0-indexed)
    # boundary[i] = 1 if there's a break AFTER slide i
    pred_boundaries = np.zeros(n_slides - 1, dtype=int)
    gt_boundaries = np.zeros(n_slides - 1, dtype=int)
    
    for b in pred_breaks:
        if 1 < b <= n_slides:  # Break at slide b means boundary after slide b-1
            pred_boundaries[b - 2] = 1
    
    for b in gt_breaks:
        if 1 < b <= n_slides:
            gt_boundaries[b - 2] = 1
    
    # Calculate window size k (half the average segment length)
    if k is None:
        n_gt_segments = len(gt_breaks) + 1
        avg_segment_len = n_slides / n_gt_segments
        k = max(2, int(avg_segment_len / 2))
    
    # Ensure k is valid
    k = min(k, n_slides - 1)
    if k < 1:
        return 0.0
    
    # Compute WindowDiff
    n_windows = n_slides - k
    if n_windows <= 0:
        return 0.0
    
    differences = 0
    for i in range(n_windows):
        # Count boundaries in window [i, i+k)
        pred_count = pred_boundaries[i:i+k].sum()
        gt_count = gt_boundaries[i:i+k].sum()
        
        # WindowDiff counts windows where boundary counts differ
        if pred_count != gt_count:
            differences += 1
    
    return differences / n_windows


def _compute_pk(
    pred_breaks: Set[int], 
    gt_breaks: Set[int], 
    n_slides: int,
    k: int = None
) -> float:
    """
    Compute Pk metric for segmentation evaluation.
    
    Pk measures the probability that two randomly chosen slides k apart
    are incorrectly classified as being in the same/different segments.
    Lower is better (0 = perfect).
    
    Args:
        pred_breaks: Set of predicted break points
        gt_breaks: Set of ground truth break points  
        n_slides: Total number of slides
        k: Window size. If None, uses half the average segment length.
        
    Returns:
        Pk score (0.0 to 1.0, lower is better)
    """
    if n_slides <= 1:
        return 0.0
    
    # Build segment assignment arrays
    # segment_id[i] = which segment slide i belongs to
    def build_segments(breaks: Set[int], n: int) -> np.ndarray:
        segments = np.zeros(n, dtype=int)
        seg_id = 0
        for i in range(n):
            slide_num = i + 1  # 1-indexed
            if slide_num in breaks:
                seg_id += 1
            segments[i] = seg_id
        return segments
    
    pred_segments = build_segments(pred_breaks, n_slides)
    gt_segments = build_segments(gt_breaks, n_slides)
    
    # Calculate window size k
    if k is None:
        n_gt_segments = len(gt_breaks) + 1
        avg_segment_len = n_slides / n_gt_segments
        k = max(2, int(avg_segment_len / 2))
    
    k = min(k, n_slides - 1)
    if k < 1:
        return 0.0
    
    # Compute Pk
    n_comparisons = n_slides - k
    if n_comparisons <= 0:
        return 0.0
    
    errors = 0
    for i in range(n_comparisons):
        j = i + k
        # Check if slides i and j are in same segment
        pred_same = (pred_segments[i] == pred_segments[j])
        gt_same = (gt_segments[i] == gt_segments[j])
        
        if pred_same != gt_same:
            errors += 1
    
    return errors / n_comparisons


def evaluate_chunking(
    predicted: List[Chunk],
    ground_truth: List[dict]
) -> Dict[str, float]:
    """
    Evaluate chunking predictions against ground truth.
    
    Metrics:
    - Precision/Recall/F1: Boundary-based metrics
    - WindowDiff: Sliding window metric (lower is better)
    - Pk: Probability of error metric (lower is better)
    
    Args:
        predicted: List of predicted Chunk objects
        ground_truth: List of ground truth dicts with chunk_id and slide_range
        
    Returns:
        Dictionary with evaluation metrics
    """
    def parse_range(s: str) -> Tuple[int, int]:
        if '-' in s:
            parts = s.split('-')
            return int(parts[0]), int(parts[1])
        return int(s), int(s)
    
    # Get all break points (slide numbers where a new chunk starts)
    pred_breaks = set()
    for chunk in predicted[1:]:  # Skip first chunk
        pred_breaks.add(chunk.start_slide)
    
    gt_breaks = set()
    max_slide = 0
    for chunk in ground_truth[1:]:
        start, end = parse_range(chunk['slide_range'])
        gt_breaks.add(start)
        max_slide = max(max_slide, end)
    
    # Get max slide from first chunk too
    if ground_truth:
        _, end = parse_range(ground_truth[0]['slide_range'])
        max_slide = max(max_slide, end)
    
    # Also check predicted chunks for n_slides
    if predicted:
        max_slide = max(max_slide, predicted[-1].end_slide)
    
    n_slides = max_slide
    
    # Compute precision, recall, F1
    if not pred_breaks:
        precision = 1.0 if not gt_breaks else 0.0
    else:
        precision = len(pred_breaks & gt_breaks) / len(pred_breaks)
    
    if not gt_breaks:
        recall = 1.0 if not pred_breaks else 0.0
    else:
        recall = len(pred_breaks & gt_breaks) / len(gt_breaks)
    
    if precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    
    # Compute WindowDiff and Pk
    windowdiff = _compute_windowdiff(pred_breaks, gt_breaks, n_slides)
    pk = _compute_pk(pred_breaks, gt_breaks, n_slides)
    
    return {
        'precision': precision,
        'recall': recall,
        'f1': f1,
        'windowdiff': windowdiff,
        'pk': pk,
        'num_predicted_chunks': len(predicted),
        'num_ground_truth_chunks': len(ground_truth),
        'predicted_breaks': sorted(pred_breaks),
        'ground_truth_breaks': sorted(gt_breaks)
    }


```

## FILE: /home/javad/NeuroNote_Pipeline/neuronote/extraction/ocr/dispatch.py
```python
"""
OCR dispatcher + unified public API.

This module is the single entry point used by the pipeline:
  - load_ocr_engine(...)
  - run_ocr(...)

It delegates to backend-specific implementations while keeping the call signature
stable for `main.py`.

Backend supported:
  - azure (Azure Document Intelligence)

Design:
  - `load_ocr_engine` returns either an engine object or a dict config depending on backend
  - `run_ocr` returns standardized detection dicts (see `src/ocr_common.py`)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import numpy as np
from PIL import Image

from .common import Detection, ImageInput
from .azure import load_azure_engine, run_azure_ocr


SUPPORTED_OCR_BACKENDS = ("azure",)


def normalize_backend_name(backend: str) -> str:
    """Normalize backend names to a canonical lowercase form."""
    return (backend or "").lower().strip()


def load_ocr_engine(backend: str = "azure", lang: str = "en", device: Optional[str] = None, **kwargs: Any) -> Any:
    """
    Load an OCR engine once and reuse across images.

    backend:
      - "azure": Azure Document Intelligence
    """
    backend = normalize_backend_name(backend)
    if backend not in set(SUPPORTED_OCR_BACKENDS):
        raise ValueError(f"Unsupported OCR backend: {backend}. Supported: {SUPPORTED_OCR_BACKENDS}")

    _ = lang
    _ = device

    if backend == "azure":
        return load_azure_engine(**kwargs)

    raise ValueError(f"Unsupported OCR backend: {backend}")


def run_ocr(
    image_input: Union[str, Image.Image, np.ndarray],
    ocr_engine: Any,
    backend: str = "azure",
    *,
    image_path: Optional[str] = None,
    output_dir: Optional[str] = None,
    azure_min_conf: float = 0.0,
    azure_level: str = "lines",
    timeout_seconds: int = 30,
    azure_retry_attempts: int = 10,
    azure_retry_backoff_seconds: float = 0.5,
) -> List[Detection]:
    """
    Run OCR and return detections in the pipeline's standard format.

    Parameters are backend-specific, but kept here so `main.py` can pass them uniformly.
    """
    backend = normalize_backend_name(backend)
    if ocr_engine is None:
        return []

    if backend == "azure":
        return run_azure_ocr(
            image_input,
            ocr_engine,
            image_path=image_path,
            output_dir=output_dir,
            azure_min_conf=azure_min_conf,
            azure_level=azure_level,
            timeout_seconds=timeout_seconds,
            retry_attempts=azure_retry_attempts,
            retry_backoff_seconds=azure_retry_backoff_seconds,
        )

    raise ValueError(f"Unsupported OCR backend: {backend}")


def is_ocr_backend_supported(backend: str) -> bool:
    """True if `backend` is a known backend name."""
    return normalize_backend_name(backend) in set(SUPPORTED_OCR_BACKENDS)


def list_supported_ocr_backends() -> List[str]:
    """Return supported backend names in a stable order."""
    return list(SUPPORTED_OCR_BACKENDS)

```

## FILE: /home/javad/NeuroNote_Pipeline/neuronote/extraction/ocr/azure.py
```python
"""
Azure Document Intelligence OCR backend.

This module provides:
  - load_azure_engine(...)
  - run_azure_ocr(...)

Uses the prebuilt-read model to extract text with bounding boxes.
"""

from __future__ import annotations

import io
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from PIL import Image

from .common import Detection, ImageInput, make_detection


def load_azure_engine(
    *,
    endpoint: Optional[str] = None,
    key: Optional[str] = None,
    **_: Any,
) -> Dict[str, Any]:
    """
    Create an Azure Document Intelligence client.
    
    Credentials are loaded from:
      1. Explicit endpoint/key parameters
      2. Environment variables AZURE_DOC_INTEL_ENDPOINT and AZURE_DOC_INTEL_KEY
      3. .env file in the project root
    """
    try:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential
    except ImportError as e:
        raise ImportError(
            "azure-ai-documentintelligence not installed. "
            "Install with: pip install azure-ai-documentintelligence"
        ) from e
    
    # Try to load from .env file
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # dotenv not installed, rely on environment variables
    
    endpoint = endpoint or os.getenv("AZURE_DOC_INTEL_ENDPOINT")
    key = key or os.getenv("AZURE_DOC_INTEL_KEY")
    
    if not endpoint or not key:
        raise ValueError(
            "Azure Document Intelligence credentials not found. "
            "Set AZURE_DOC_INTEL_ENDPOINT and AZURE_DOC_INTEL_KEY environment variables "
            "or pass them explicitly."
        )
    
    client = DocumentIntelligenceClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(key),
    )
    
    return {"client": client}


def _to_png_bytes(image_input: ImageInput) -> bytes:
    """Convert image input to PNG bytes."""
    if isinstance(image_input, str):
        img = Image.open(image_input).convert("RGB")
    elif isinstance(image_input, Image.Image):
        img = image_input.convert("RGB")
    elif isinstance(image_input, np.ndarray):
        img = Image.fromarray(image_input).convert("RGB")
    else:
        raise TypeError("Unsupported image_input type for Azure OCR")
    
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _iter_polygon_xy(polygon: Any) -> List[Tuple[float, float]]:
    """
    Normalize Azure polygon representations into a list of (x, y) floats.

    Azure SDKs have varied over time. We support:
      - Flat list: [x1, y1, x2, y2, ...]
      - List of points: [(x, y), ...] / [{'x':..,'y':..}, ...] / objects with .x/.y
    """
    if not polygon:
        return []

    # Flat list of numbers (x1, y1, x2, y2, ...)
    if isinstance(polygon, list) and polygon and all(isinstance(v, (int, float)) for v in polygon):
        pts: List[Tuple[float, float]] = []
        for i in range(0, len(polygon) - 1, 2):
            pts.append((float(polygon[i]), float(polygon[i + 1])))
        return pts

    pts2: List[Tuple[float, float]] = []
    if isinstance(polygon, Iterable):
        for pt in polygon:
            if pt is None:
                continue
            if isinstance(pt, dict) and "x" in pt and "y" in pt:
                pts2.append((float(pt["x"]), float(pt["y"])))
                continue
            if hasattr(pt, "x") and hasattr(pt, "y"):
                pts2.append((float(getattr(pt, "x")), float(getattr(pt, "y"))))
                continue
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                pts2.append((float(pt[0]), float(pt[1])))
                continue
    return pts2


def _scale_points_if_normalized(
    pts: List[Tuple[float, float]], img_width: int, img_height: int
) -> List[Tuple[float, float]]:
    if not pts:
        return pts
    max_x = max(x for x, _ in pts)
    max_y = max(y for _, y in pts)
    if max_x <= 1.0 and max_y <= 1.0:
        return [(x * img_width, y * img_height) for x, y in pts]
    return pts


def _points_to_bbox(pts: List[Tuple[float, float]]) -> Optional[List[float]]:
    if not pts:
        return None
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _polygon_to_points(polygon: Any, img_width: int, img_height: int) -> Optional[List[List[int]]]:
    pts = _scale_points_if_normalized(_iter_polygon_xy(polygon), img_width, img_height)
    if len(pts) < 3:
        return None
    out: List[List[int]] = []
    for x, y in pts:
        xi = int(round(max(0.0, min(float(x), float(img_width)))))
        yi = int(round(max(0.0, min(float(y), float(img_height)))))
        out.append([xi, yi])
    return out if len(out) >= 3 else None


def _polygon_to_bbox(polygon: Any, img_width: int, img_height: int) -> Optional[List[float]]:
    """
    Convert Azure's polygon (list of x,y pairs) to [x1, y1, x2, y2] bbox.
    Azure returns normalized coordinates (0-1), so we scale to image dimensions.
    """
    pts = _scale_points_if_normalized(_iter_polygon_xy(polygon), img_width, img_height)
    return _points_to_bbox(pts)


def run_azure_ocr(
    image_input: ImageInput,
    engine: Dict[str, Any],
    *,
    image_path: Optional[str] = None,
    output_dir: Optional[str] = None,
    azure_min_conf: float = 0.0,
    azure_level: str = "lines",
    timeout_seconds: int = 30,
    retry_attempts: int = 10,
    retry_backoff_seconds: float = 0.5,
) -> List[Detection]:
    """
    Run Azure Document Intelligence OCR and return detections.
    
    azure_level can be:
      - "lines" (default) - individual text lines
      - "words" - individual words
      - "paragraphs" - paragraph groupings (if available)
    """
    try:
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
    except ImportError as e:
        print(f"  Azure Error: azure-ai-documentintelligence not installed: {e}")
        return []
    
    client = engine.get("client")
    if client is None:
        print("  Azure Error: invalid engine (missing client)")
        return []
    
    # Get image dimensions
    if isinstance(image_input, str):
        img = Image.open(image_input)
    elif isinstance(image_input, Image.Image):
        img = image_input
    elif isinstance(image_input, np.ndarray):
        img = Image.fromarray(image_input)
    else:
        print("  Azure Error: unsupported image type")
        return []
    
    img_width, img_height = img.size
    
    # Convert to bytes
    content = _to_png_bytes(image_input)
    
    result = None
    attempt_errors: List[str] = []

    def _call_analyze_with_base64():
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
        import base64

        request = AnalyzeDocumentRequest(
            bytes_source=base64.b64encode(content).decode("utf-8")
        )
        poller = client.begin_analyze_document(
            model_id="prebuilt-read",
            body=request,
        )
        return poller.result(timeout=timeout_seconds)

    def _call_analyze_with_raw_bytes():
        poller = client.begin_analyze_document(
            model_id="prebuilt-read",
            body=content,
        )
        return poller.result(timeout=timeout_seconds)

    max_retries = max(1, int(retry_attempts))
    base_backoff = max(0.0, float(retry_backoff_seconds))
    attempts = [
        ("base64 request", _call_analyze_with_base64),
        ("raw-bytes request", _call_analyze_with_raw_bytes),
    ]

    # Retry multiple times, cycling request formats, until a valid result is returned.
    for retry_idx in range(max_retries):
        for attempt_name, fn in attempts:
            try:
                result = fn()
                if result is None:
                    attempt_errors.append(
                        f"retry {retry_idx + 1}/{max_retries} {attempt_name}: returned None result"
                    )
                    continue
                # Some SDK responses may produce a non-None object with no pages.
                if not getattr(result, "pages", None):
                    attempt_errors.append(
                        f"retry {retry_idx + 1}/{max_retries} {attempt_name}: result had no pages"
                    )
                    result = None
                    continue
                break
            except TimeoutError:
                attempt_errors.append(
                    f"retry {retry_idx + 1}/{max_retries} {attempt_name}: timed out after {timeout_seconds}s"
                )
            except Exception as e:
                attempt_errors.append(f"retry {retry_idx + 1}/{max_retries} {attempt_name}: {e}")

        if result is not None:
            break

        if retry_idx < max_retries - 1 and base_backoff > 0:
            sleep_s = base_backoff * (retry_idx + 1)
            print(f"  Azure OCR retry {retry_idx + 1}/{max_retries} failed; retrying in {sleep_s:.1f}s...")
            time.sleep(sleep_s)

    # Save raw OCR result if output directory is provided
    if output_dir and result is not None:
        import json
        raw_output_path = os.path.join(output_dir, "raw_ocr_azure.json")
        try:
            raw_payload = result.as_dict() if hasattr(result, "as_dict") else None
            if raw_payload is not None:
                with open(raw_output_path, "w", encoding="utf-8") as f:
                    json.dump(raw_payload, f, indent=2)
        except Exception as save_err:
            # Some SDK versions can fail on as_dict() for partial/null sub-objects.
            # Keep OCR processing alive even if raw debug serialization fails.
            print(f"  Azure Warning: failed to save raw OCR JSON: {save_err}")
    
    dets: List[Detection] = []
    azure_level = (azure_level or "lines").lower().strip()
    
    if result is None:
        if attempt_errors:
            print("  Azure Error: API returned None result")
            for err in attempt_errors:
                print(f"    - {err}")
            if output_dir:
                try:
                    err_path = os.path.join(output_dir, "raw_ocr_azure_error.txt")
                    with open(err_path, "w", encoding="utf-8") as f:
                        f.write("Azure OCR failed to produce a result.\n")
                        for err in attempt_errors:
                            f.write(f"{err}\n")
                except Exception:
                    pass
        else:
            print("  Azure Error: API returned None result")
        return []
    
    for page in result.pages:
        page_width = page.width or img_width
        page_height = page.height or img_height
        
        if azure_level == "words":
            # Extract individual words
            for word in (page.words or []):
                text = word.content or ""
                conf = word.confidence or 0.0
                if conf < azure_min_conf or not text.strip():
                    continue
                
                bbox = _polygon_to_bbox(word.polygon, page_width, page_height)
                if bbox:
                    poly = _polygon_to_points(word.polygon, page_width, page_height)
                    extra: Dict[str, Any] = {"ocr_unit": "word"}
                    if poly:
                        extra["polygon"] = poly
                    dets.append(make_detection(
                        bbox=bbox,
                        text=text.strip(),
                        score=conf,
                        **extra,
                    ))
        
        elif azure_level == "lines":
            # Extract text lines
            for line in (page.lines or []):
                text = line.content or ""
                if not text.strip():
                    continue
                
                # Lines don't have confidence, estimate from words
                line_words = [w for w in (page.words or []) 
                             if _word_in_line(w, line)]
                if line_words:
                    conf = sum(w.confidence or 0.0 for w in line_words) / len(line_words)
                else:
                    conf = 1.0  # Assume high confidence if no word info
                
                if conf < azure_min_conf:
                    continue
                
                bbox = _polygon_to_bbox(line.polygon, page_width, page_height)
                if bbox:
                    poly = _polygon_to_points(line.polygon, page_width, page_height)
                    extra = {"ocr_unit": "line"}
                    if poly:
                        extra["polygon"] = poly
                    dets.append(make_detection(
                        bbox=bbox,
                        text=text.strip(),
                        score=conf,
                        **extra,
                    ))
        
        elif azure_level == "paragraphs":
            # Extract paragraphs (from pages or document structure)
            # Paragraphs are in result.paragraphs if available
            pass  # Fall through to document-level paragraphs
    
            # Handle paragraphs at document level
            if hasattr(result, 'paragraphs') and result.paragraphs:
                # Collect all words from all pages into a single flat list for span matching
                all_words = []
                if result.pages:
                    for p in result.pages:
                        if p.words:
                            all_words.extend(p.words)

                for para in result.paragraphs:
                    text = para.content or ""
                    if not text.strip():
                        continue
                    
                    # collect words belonging to this paragraph
                    para_words = []
                    if hasattr(para, "spans") and para.spans:
                        for span in para.spans:
                            span_start = span.offset
                            span_length = span.length
                            span_end = span_start + span_length
                            
                            # Find words within this span
                            # Optimization: could binary search if sorted, but linear is fine for doc scale
                            for w in all_words:
                                if hasattr(w, "span"):
                                    w_start = w.span.offset
                                    w_end = w_start + w.span.length
                                    # Check if word is inside paragraph span
                                    if w_start >= span_start and w_end <= span_end:
                                        para_words.append(w)

                    # Paragraphs don't have confidence scores
                    conf = 1.0
                    
                    bbox = None
                    poly = None
                    page_width = img_width
                    page_height = img_height
                    
                    if para.bounding_regions:
                        # Use first bounding region
                        region = para.bounding_regions[0]
                        page_idx = (region.page_number or 1) - 1
                        if result.pages and page_idx < len(result.pages):
                            page = result.pages[page_idx]
                            page_width = page.width or img_width
                            page_height = page.height or img_height
                            bbox = _polygon_to_bbox(region.polygon, page_width, page_height)
                            poly = _polygon_to_points(region.polygon, page_width, page_height)
                    
                    if bbox:
                        extra = {"ocr_unit": "paragraph"}
                        if poly:
                            extra["polygon"] = poly
                        
                        # Convert Azure words to our dict format
                        constituent_words = []
                        for w in para_words:
                            w_text = w.content
                            w_bbox = _polygon_to_bbox(w.polygon, page_width, page_height)
                            if w_text and w_bbox:
                                constituent_words.append({
                                    "text_content": w_text,
                                    "bbox": w_bbox,
                                    "polygon": _polygon_to_points(w.polygon, page_width, page_height)
                                })
                        
                        if constituent_words:
                             extra["words"] = constituent_words

                        dets.append(make_detection(
                            bbox=bbox,
                            text=text.strip(),
                            score=conf,
                            **extra,
                        ))
    
    # If paragraphs requested but none found, fall back to lines
    if azure_level == "paragraphs" and not dets:
        print("  Azure: No paragraphs found, falling back to lines")
        return run_azure_ocr(
            image_input, engine,
            image_path=image_path,
            output_dir=output_dir,
            azure_min_conf=azure_min_conf,
            azure_level="lines",
            timeout_seconds=timeout_seconds,
        )
    
    return dets


def _word_in_line(word, line) -> bool:
    """Check if a word belongs to a line (rough check based on content)."""
    if not word.content or not line.content:
        return False
    return word.content in line.content

```

