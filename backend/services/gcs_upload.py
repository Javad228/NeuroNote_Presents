from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from typing import Any


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class GCSUploadService:
    def __init__(self, *, bucket_name: str, object_prefix: str = "") -> None:
        bucket_name = bucket_name.strip()
        if not bucket_name:
            raise ValueError("GCS bucket name cannot be empty.")

        self.bucket_name = bucket_name
        self.object_prefix = object_prefix.strip("/")

    def _create_storage_client(self) -> Any:
        try:
            from google.cloud import storage
        except Exception as exc:
            raise RuntimeError(
                "google-cloud-storage is required for uploads. Install with "
                "`pip install google-cloud-storage`."
            ) from exc

        return storage.Client()

    def _collect_images(self, images_dir: Path) -> list[Path]:
        if not images_dir.exists() or not images_dir.is_dir():
            raise RuntimeError(f"Images directory does not exist: {images_dir}")

        image_paths = sorted(
            path
            for path in images_dir.iterdir()
            if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
        )
        if not image_paths:
            raise RuntimeError(f"No slide images found in {images_dir}")
        return image_paths

    def _job_folder(self, job_id: str) -> str:
        if self.object_prefix:
            return f"{self.object_prefix}/{job_id}"
        return job_id

    def bucket_path_for_job(self, job_id: str) -> str:
        return f"gs://{self.bucket_name}/{self._job_folder(job_id)}"

    def upload_images(self, *, job_id: str, images_dir: Path) -> list[dict[str, str]]:
        storage_client = self._create_storage_client()
        bucket = storage_client.bucket(self.bucket_name)
        job_folder = self._job_folder(job_id)

        uploaded: list[dict[str, str]] = []
        for image_path in self._collect_images(images_dir):
            blob_name = f"{job_folder}/{image_path.name}"
            blob = bucket.blob(blob_name)
            content_type, _ = mimetypes.guess_type(image_path.name)
            blob.upload_from_filename(
                str(image_path),
                content_type=content_type or "application/octet-stream",
            )
            uploaded.append(
                {
                    "filename": image_path.name,
                    "object_path": image_path.name,
                    "blob_name": blob_name,
                    "gs_uri": f"gs://{self.bucket_name}/{blob_name}",
                }
            )

        return uploaded

    async def upload_images_async(self, *, job_id: str, images_dir: Path) -> list[dict[str, str]]:
        return await asyncio.to_thread(
            self.upload_images,
            job_id=job_id,
            images_dir=images_dir,
        )

    def folder_for_job(self, job_id: str) -> str:
        return self._job_folder(job_id)
