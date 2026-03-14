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
                    "SlideParser /api/process-batch-gcs response is missing job_id/batch_id."
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
                f"SlideParser batch job {status_payload.get('job_id', 'unknown')} "
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
            "requested": {
                "provider": options.tts_provider,
                "model": options.tts_model,
                "voice": options.tts_voice,
                "elevenlabs_output_format": options.tts_elevenlabs_output_format,
            },
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
                    tts_provider=options.tts_provider,
                    tts_model=options.tts_model,
                    tts_voice=options.tts_voice,
                    tts_elevenlabs_output_format=options.tts_elevenlabs_output_format,
                )
                audio_elapsed_s = time.perf_counter() - audio_started
                transcript_audio = {
                    "enabled": True,
                    "status": "complete",
                    "elapsed_s": round(audio_elapsed_s, 3),
                    "requested": {
                        "provider": options.tts_provider,
                        "model": options.tts_model,
                        "voice": options.tts_voice,
                        "elevenlabs_output_format": options.tts_elevenlabs_output_format,
                    },
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
                    "requested": {
                        "provider": options.tts_provider,
                        "model": options.tts_model,
                        "voice": options.tts_voice,
                        "elevenlabs_output_format": options.tts_elevenlabs_output_format,
                    },
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
