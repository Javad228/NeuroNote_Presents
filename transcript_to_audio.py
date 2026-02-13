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
DEFAULT_VOICE = "alloy"
DEFAULT_MAX_CHARS = 3500
OPENAI_AUDIO_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
SLIDE_INDEX_RE = re.compile(r"page_(\d+)$")


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


def resolve_output_roots(neuronote_pipeline_root: Path) -> list[Path]:
    return [
        (neuronote_pipeline_root / "neuronote" / "jobs").resolve(),
        (neuronote_pipeline_root / "jobs").resolve(),
        neuronote_pipeline_root.resolve(),
    ]


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


def extract_step_items_from_job(job_dir: Path, neuronote_pipeline_root: Path) -> list[dict[str, Any]]:
    result_path = job_dir / "result.json"
    if not result_path.exists():
        raise FileNotFoundError(f"Missing result.json in job: {job_dir}")

    payload = read_json(result_path)
    chunks = payload.get("neuronote_chunks")
    if not isinstance(chunks, list):
        raise RuntimeError("result.json has no neuronote_chunks list")

    output_roots = resolve_output_roots(neuronote_pipeline_root)
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
            script_url = image.get("script_url")
            if not isinstance(image_name, str) or not isinstance(script_url, str):
                continue

            match = SLIDE_INDEX_RE.match(image_name)
            if not match:
                continue
            slide_number = int(match.group(1))

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
                        "image_name": image_name,
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

    job_dir: Path | None = None
    output_path: Path

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
    job_dir = resolve_job_dir(jobs_root, job_id)

    step_items = extract_step_items_from_job(
        job_dir=job_dir,
        neuronote_pipeline_root=pipeline_root,
    )

    transcript_text = build_transcript_text(
        step_items,
        include_slide_headings=not args.no_slide_headings,
    )
    (job_dir / "transcript.txt").write_text(transcript_text)

    all_step_wavs: list[bytes] = []
    timing_steps: list[dict[str, Any]] = []
    current_ms = 0
    total_chunks = 0

    for idx, item in enumerate(step_items, start=1):
        line = str(item["line"])
        print(
            "Synthesizing step "
            f"{idx}/{len(step_items)} "
            f"(slide {item['slide_number']} step {item['step_number']})"
        )

        step_wav, chunk_count = synthesize_text_to_wav(
            api_key=api_key,
            model=args.model,
            voice=args.voice,
            text=line,
            max_chars=args.max_chars,
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
    output_path = resolve_output_path(args, job_dir)
    write_wav_file(merged_wav, output_path)

    timestamps_path = resolve_timestamps_path(args, job_dir)
    if timestamps_path is not None:
        timestamps_path.parent.mkdir(parents=True, exist_ok=True)
        timestamps_payload = {
            "job_id": job_id,
            "audio_file": output_path.name,
            "model": args.model,
            "voice": args.voice,
            "steps": timing_steps,
            "total_duration_ms": current_ms,
        }
        timestamps_path.write_text(json.dumps(timestamps_payload, indent=2))
        print(f"Timestamps written to: {timestamps_path}")

    print(f"Audio written to: {output_path}")
    print(f"Steps: {len(step_items)}")
    print(f"Chunks: {total_chunks}")
    print(f"Characters: {len(' '.join(transcript_text.split()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
