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
