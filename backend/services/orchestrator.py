import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..schemas import ProcessPdfOptions
from .chunking import ChunkingService
from .neuronote_client import NeuroNoteClient
from .pdf_render import render_pdf_to_images

logger = logging.getLogger(__name__)


class OrchestratorService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.chunking_service = ChunkingService(
            chunking_root=config.chunking_root,
            neuronote_pipeline_root=config.neuronote_pipeline_root,
            azure_ocr_level=config.azure_ocr_level,
            azure_ocr_min_conf=config.azure_ocr_min_conf,
        )

    async def _process_chunk(
        self,
        *,
        neuronote: NeuroNoteClient,
        chunk: dict[str, Any],
        images_dir: Path,
        options: ProcessPdfOptions,
    ) -> dict[str, Any]:
        chunk_images = self.chunking_service.collect_chunk_images(
            images_dir=images_dir,
            slide_range=chunk["slide_range"],
        )

        if len(chunk_images) == 1:
            endpoint = "/api/process"
            upstream_response = await neuronote.process_single(
                chunk_images[0],
                skip_generation=options.skip_generation,
                previous_context=options.previous_context,
            )
        else:
            endpoint = "/api/process-batch"
            upstream_response = await neuronote.process_batch(
                chunk_images,
                skip_generation=options.skip_generation,
            )

        return {
            "chunk_id": chunk["chunk_id"],
            "slide_range": chunk["slide_range"],
            "num_slides": len(chunk_images),
            "endpoint_used": endpoint,
            "neuronote": upstream_response,
        }

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

        chunking = await self.chunking_service.run_changepoint_chunking(
            slide_dir=slide_dir,
            method=options.method,
            penalty=options.penalty,
            n_bkps=options.n_bkps,
            min_chunk=options.min_chunk,
            use_embeddings=options.use_embeddings,
            use_cache=options.use_cache,
        )

        neuronote_results = []
        chunks = chunking.get("chunks", [])
        logger.info(
            "[parallel] dispatch_start job=%s chunk_count=%d",
            job_id,
            len(chunks),
        )
        inflight = {"count": 0, "max": 0}
        inflight_lock = asyncio.Lock()

        async with NeuroNoteClient(
            base_url=self.config.neuronote_api_base,
            timeout_seconds=self.config.neuronote_timeout_seconds,
        ) as neuronote:
            async def run_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
                chunk_id = str(chunk.get("chunk_id", "unknown"))
                slide_range = str(chunk.get("slide_range", "unknown"))
                chunk_started = time.perf_counter()

                async with inflight_lock:
                    inflight["count"] += 1
                    inflight["max"] = max(inflight["max"], inflight["count"])
                    inflight_now = inflight["count"]

                logger.info(
                    "[parallel] chunk_start job=%s chunk=%s range=%s inflight=%d",
                    job_id,
                    chunk_id,
                    slide_range,
                    inflight_now,
                )

                try:
                    result = await self._process_chunk(
                        neuronote=neuronote,
                        chunk=chunk,
                        images_dir=images_dir,
                        options=options,
                    )
                    elapsed_s = time.perf_counter() - chunk_started
                    logger.info(
                        "[parallel] chunk_done job=%s chunk=%s endpoint=%s slides=%s elapsed_s=%.2f",
                        job_id,
                        chunk_id,
                        result.get("endpoint_used", "unknown"),
                        result.get("num_slides", "unknown"),
                        elapsed_s,
                    )
                    return result
                except Exception:
                    elapsed_s = time.perf_counter() - chunk_started
                    logger.exception(
                        "[parallel] chunk_fail job=%s chunk=%s range=%s elapsed_s=%.2f",
                        job_id,
                        chunk_id,
                        slide_range,
                        elapsed_s,
                    )
                    raise
                finally:
                    async with inflight_lock:
                        inflight["count"] -= 1
                        inflight_now = inflight["count"]
                    logger.info(
                        "[parallel] chunk_release job=%s chunk=%s inflight=%d",
                        job_id,
                        chunk_id,
                        inflight_now,
                    )

            tasks = [
                asyncio.create_task(
                    run_chunk(chunk)
                )
                for chunk in chunks
            ]

            if tasks:
                task_results = await asyncio.gather(*tasks, return_exceptions=True)
                errors: list[str] = []
                for chunk, result in zip(chunks, task_results):
                    if isinstance(result, Exception):
                        errors.append(f'{chunk.get("chunk_id", "unknown")}: {result}')
                        continue
                    neuronote_results.append(result)

                if errors:
                    joined = "; ".join(errors[:5])
                    if len(errors) > 5:
                        joined = f"{joined}; +{len(errors) - 5} more"
                    logger.error(
                        "[parallel] dispatch_fail job=%s failed_chunks=%d/%d details=%s",
                        job_id,
                        len(errors),
                        len(chunks),
                        joined,
                    )
                    raise RuntimeError(
                        f"Failed processing {len(errors)}/{len(chunks)} chunks in parallel: {joined}"
                    )

        total_elapsed_s = time.perf_counter() - process_started
        logger.info(
            "[parallel] dispatch_done job=%s chunk_count=%d max_inflight=%d elapsed_s=%.2f",
            job_id,
            len(chunks),
            inflight["max"],
            total_elapsed_s,
        )

        final_result = {
            "job_id": job_id,
            "working_dir": str(job_dir),
            "input_pdf": str(pdf_path),
            "page_count": page_count,
            "chunking": chunking,
            "neuronote_chunks": neuronote_results,
        }

        (job_dir / "result.json").write_text(json.dumps(final_result, indent=2))
        return final_result
