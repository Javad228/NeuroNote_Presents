from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import AppConfig


_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class JobsService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.jobs_root = config.jobs_root

    def _is_valid_job_id(self, job_id: str) -> bool:
        return bool(_JOB_ID_RE.match(job_id))

    def resolve_job_dir(self, job_id: str) -> Path | None:
        if not self._is_valid_job_id(job_id):
            return None

        candidate = (self.jobs_root / job_id).resolve()
        try:
            candidate.relative_to(self.jobs_root.resolve())
        except Exception:
            return None

        if not candidate.exists() or not candidate.is_dir():
            return None

        return candidate

    @staticmethod
    def _to_iso(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            return int(value)
        except Exception:
            return None

    def _detect_thumbnail_path(self, job_dir: Path) -> Path | None:
        images_dir = job_dir / "slides" / "images"
        if not images_dir.exists() or not images_dir.is_dir():
            return None

        page_one = images_dir / "page_001.png"
        if page_one.exists() and page_one.is_file():
            return page_one

        candidates = sorted(images_dir.glob("page_*.png"))
        return candidates[0] if candidates else None

    def _extract_job_metadata(self, job_dir: Path) -> dict[str, Any] | None:
        result_path = job_dir / "result.json"
        input_pdf_path = job_dir / "input.pdf"
        thumbnail_path = self._detect_thumbnail_path(job_dir)

        result_data: dict[str, Any] | None = None
        status = "partial"

        if result_path.exists() and result_path.is_file():
            try:
                result_data = json.loads(result_path.read_text())
                if isinstance(result_data, dict):
                    status = "complete"
                else:
                    result_data = None
            except Exception:
                result_data = None

        has_job_artifacts = (
            result_path.exists()
            or input_pdf_path.exists()
            or (job_dir / "slides").exists()
            or thumbnail_path is not None
        )
        if not has_job_artifacts:
            return None

        input_pdf_name: str | None = None
        title: str | None = None

        if input_pdf_path.exists():
            input_pdf_name = input_pdf_path.name
            title = input_pdf_path.stem

        if not input_pdf_name and result_data:
            maybe_pdf = result_data.get("input_pdf")
            if isinstance(maybe_pdf, str) and maybe_pdf.strip():
                input_pdf_name = Path(maybe_pdf).name
                title = Path(maybe_pdf).stem

        if not input_pdf_name:
            input_pdf_name = f"{job_dir.name}.pdf"
        if not title:
            title = job_dir.name

        page_count = self._safe_int(result_data.get("page_count") if result_data else None)

        chunk_count: int | None = None
        if result_data:
            chunking = result_data.get("chunking")
            if isinstance(chunking, dict):
                chunks = chunking.get("chunks")
                if isinstance(chunks, list):
                    chunk_count = len(chunks)

        created_ts = input_pdf_path.stat().st_mtime if input_pdf_path.exists() else job_dir.stat().st_mtime
        if result_path.exists():
            updated_ts = result_path.stat().st_mtime
        else:
            updated_ts = job_dir.stat().st_mtime

        return {
            "job_id": job_dir.name,
            "title": title,
            "input_pdf_name": input_pdf_name,
            "created_at": self._to_iso(created_ts),
            "updated_at": self._to_iso(updated_ts),
            "page_count": page_count,
            "chunk_count": chunk_count,
            "thumbnail_url": f"/api/jobs/{job_dir.name}/thumbnail" if thumbnail_path else None,
            "status": status,
            "_updated_ts": updated_ts,
        }

    def list_jobs(self) -> list[dict[str, Any]]:
        if not self.jobs_root.exists() or not self.jobs_root.is_dir():
            return []

        jobs: list[dict[str, Any]] = []

        for entry in self.jobs_root.iterdir():
            if not entry.is_dir():
                continue

            metadata = self._extract_job_metadata(entry)
            if metadata is None:
                continue
            jobs.append(metadata)

        jobs.sort(key=lambda item: item.get("_updated_ts", 0), reverse=True)

        for item in jobs:
            item.pop("_updated_ts", None)

        return jobs

    def get_thumbnail_path(self, job_id: str) -> Path | None:
        job_dir = self.resolve_job_dir(job_id)
        if job_dir is None:
            return None

        return self._detect_thumbnail_path(job_dir)
