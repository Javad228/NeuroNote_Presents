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

    async def _post_with_logging(
        self,
        *,
        url: str,
        log_context: str,
        started: float,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            return await self.client.post(url, **kwargs)
        except httpx.TimeoutException as exc:
            elapsed_s = time.perf_counter() - started
            logger.error(
                "[neuronote] request_timeout %s base_url=%s elapsed_s=%.2f error=%s",
                log_context,
                self.base_url,
                elapsed_s,
                exc,
            )
            raise RuntimeError(
                f"SlideParser request timed out for {url} after {elapsed_s:.2f}s: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            elapsed_s = time.perf_counter() - started
            logger.error(
                "[neuronote] request_transport_fail %s base_url=%s elapsed_s=%.2f error=%s",
                log_context,
                self.base_url,
                elapsed_s,
                exc,
            )
            raise RuntimeError(
                f"SlideParser request failed for {url} after {elapsed_s:.2f}s: {exc}"
            ) from exc

    async def _get_with_logging(
        self,
        *,
        url: str,
        log_context: str,
        started: float,
    ) -> httpx.Response:
        try:
            return await self.client.get(url)
        except httpx.TimeoutException as exc:
            elapsed_s = time.perf_counter() - started
            logger.error(
                "[neuronote] request_timeout %s base_url=%s elapsed_s=%.2f error=%s",
                log_context,
                self.base_url,
                elapsed_s,
                exc,
            )
            raise RuntimeError(
                f"SlideParser request timed out for {url} after {elapsed_s:.2f}s: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            elapsed_s = time.perf_counter() - started
            logger.error(
                "[neuronote] request_transport_fail %s base_url=%s elapsed_s=%.2f error=%s",
                log_context,
                self.base_url,
                elapsed_s,
                exc,
            )
            raise RuntimeError(
                f"SlideParser request failed for {url} after {elapsed_s:.2f}s: {exc}"
            ) from exc

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

        response = await self._post_with_logging(
            url=url,
            log_context=f"endpoint=/api/process image={image_path.name}",
            started=started,
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
                f"SlideParser single API returned {response.status_code}: {response.text[:400]}"
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

        response = await self._post_with_logging(
            url=url,
            log_context=f"endpoint=/api/process-batch images={len(image_paths)}",
            started=started,
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
                f"SlideParser batch API returned {response.status_code}: {response.text[:400]}"
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
        response = await self._post_with_logging(
            url=url,
            log_context=f"endpoint=/api/process-batch-gcs chunks={len(chunks)}",
            started=started,
            json=payload,
        )
        elapsed_s = time.perf_counter() - started
        if response.status_code >= 400:
            logger.error(
                "[neuronote] request_fail endpoint=/api/process-batch-gcs status=%d elapsed_s=%.2f",
                response.status_code,
                elapsed_s,
            )
            raise RuntimeError(
                f"SlideParser GCS batch API returned {response.status_code}: {response.text[:400]}"
            )

        logger.info(
            "[neuronote] request_done endpoint=/api/process-batch-gcs status=%d elapsed_s=%.2f",
            response.status_code,
            elapsed_s,
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("SlideParser GCS batch API returned a non-object payload.")
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
        response = await self._post_with_logging(
            url=url,
            log_context=f"endpoint=/api/process-batch-gcs object_paths={len(cleaned)}",
            started=started,
            json=payload,
        )
        elapsed_s = time.perf_counter() - started
        if response.status_code >= 400:
            logger.error(
                "[neuronote] request_fail endpoint=/api/process-batch-gcs status=%d elapsed_s=%.2f",
                response.status_code,
                elapsed_s,
            )
            raise RuntimeError(
                f"SlideParser GCS batch API returned {response.status_code}: {response.text[:400]}"
            )

        logger.info(
            "[neuronote] request_done endpoint=/api/process-batch-gcs status=%d elapsed_s=%.2f",
            response.status_code,
            elapsed_s,
        )
        result_payload = response.json()
        if not isinstance(result_payload, dict):
            raise RuntimeError("SlideParser GCS batch API returned a non-object payload.")
        return result_payload

    async def get_job(self, *, job_id: str) -> dict[str, Any]:
        started = time.perf_counter()
        url = f"{self.base_url}/api/jobs/{job_id}"
        response = await self._get_with_logging(
            url=url,
            log_context=f"endpoint=/api/jobs/{job_id}",
            started=started,
        )
        elapsed_s = time.perf_counter() - started
        if response.status_code >= 400:
            raise RuntimeError(
                f"SlideParser job status API returned {response.status_code}: {response.text[:400]}"
            )

        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("SlideParser job status API returned a non-object payload.")

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
                    f"Timed out waiting for SlideParser job {job_id} (last status={status or 'unknown'})."
                )

            await asyncio.sleep(poll_every)
