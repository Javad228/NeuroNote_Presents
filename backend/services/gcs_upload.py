from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from posixpath import normpath
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

    @staticmethod
    def _normalize_object_path(value: str) -> str:
        candidate = str(value or "").strip().replace("\\", "/").lstrip("/")
        if not candidate:
            raise RuntimeError("Upload object path cannot be empty.")
        normalized = normpath(candidate).replace("\\", "/")
        parts = [part for part in normalized.split("/") if part not in {"", "."}]
        if not parts or any(part == ".." for part in parts):
            raise RuntimeError(f"Invalid upload object path: {value!r}")
        return "/".join(parts)

    def upload_named_files(
        self,
        *,
        job_id: str,
        uploads: list[tuple[Path, str]],
    ) -> list[dict[str, str]]:
        storage_client = self._create_storage_client()
        bucket = storage_client.bucket(self.bucket_name)
        job_folder = self._job_folder(job_id)

        uploaded: list[dict[str, str]] = []
        for file_path, object_path in uploads:
            if not file_path.exists() or not file_path.is_file():
                raise RuntimeError(f"Upload file does not exist: {file_path}")
            normalized_object_path = self._normalize_object_path(object_path)
            blob_name = f"{job_folder}/{normalized_object_path}"
            blob = bucket.blob(blob_name)
            content_type, _ = mimetypes.guess_type(file_path.name)
            blob.upload_from_filename(
                str(file_path),
                content_type=content_type or "application/octet-stream",
            )
            uploaded.append(
                {
                    "filename": file_path.name,
                    "object_path": normalized_object_path,
                    "blob_name": blob_name,
                    "gs_uri": f"gs://{self.bucket_name}/{blob_name}",
                }
            )

        return uploaded

    def upload_images(self, *, job_id: str, images_dir: Path) -> list[dict[str, str]]:
        return self.upload_named_files(
            job_id=job_id,
            uploads=[(image_path, image_path.name) for image_path in self._collect_images(images_dir)],
        )

    async def upload_images_async(self, *, job_id: str, images_dir: Path) -> list[dict[str, str]]:
        return await asyncio.to_thread(
            self.upload_images,
            job_id=job_id,
            images_dir=images_dir,
        )

    async def upload_named_files_async(
        self,
        *,
        job_id: str,
        uploads: list[tuple[Path, str]],
    ) -> list[dict[str, str]]:
        return await asyncio.to_thread(
            self.upload_named_files,
            job_id=job_id,
            uploads=uploads,
        )

    def folder_for_job(self, job_id: str) -> str:
        return self._job_folder(job_id)
