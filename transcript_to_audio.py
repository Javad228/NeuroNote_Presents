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
