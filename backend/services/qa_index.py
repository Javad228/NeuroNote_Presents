from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from rank_bm25 import BM25Okapi
except Exception:
    BM25Okapi = None  # type: ignore[assignment]

from ..config import AppConfig
from .jobs import JobsService
from .lecture import LectureService
from .openai_qa import OpenAIQAService


logger = logging.getLogger(__name__)

_INDEX_VERSION_V1 = "explanation_index_v1"
_INDEX_VERSION_V2 = "explanation_index_v2_visual_step_bbox"
_TOKENIZER_VERSION = "v1"
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_RRF_K = 60

_V2_FIELD_WEIGHTS: dict[str, float] = {
    "dense_core": 1.00,
    "bm25_core": 0.95,
    "dense_script_aug": 0.55,
    "bm25_script_aug": 0.40,
    "dense_visual_step": 0.48,
    "bm25_visual_step": 0.34,
    "dense_region_aux": 0.18,
    "bm25_region_aux": 0.15,
}
_V2_QUERY_WEIGHT_ORIGINAL = 1.00
_V2_QUERY_WEIGHT_REWRITE = 0.70


class QaIndexUnavailableError(RuntimeError):
    pass


@dataclass
class LoadedQaIndex:
    payload: dict[str, Any]
    index_version: str
    units: list[dict[str, Any]]
    embeddings: list[list[float]]
    unit_tokens: list[list[str]]
    bm25: Any
    region_catalog: list[dict[str, Any]]
    slides: list[dict[str, Any]]
    unit_by_id: dict[str, dict[str, Any]]
    region_lookup: dict[tuple[str, str], dict[str, Any]]
    # V2 fields (optional on v1 runtime)
    unit_text_core: list[str] | None = None
    unit_text_script_aug: list[str] | None = None
    unit_text_visual_step: list[str] | None = None
    unit_text_region_aux: list[str] | None = None
    embeddings_core: list[list[float]] | None = None
    embeddings_script_aug: list[list[float]] | None = None
    embeddings_visual_step: list[list[float]] | None = None
    embeddings_region_aux: list[list[float]] | None = None
    unit_tokens_core: list[list[str]] | None = None
    unit_tokens_script_aug: list[list[str]] | None = None
    unit_tokens_visual_step: list[list[str]] | None = None
    unit_tokens_region_aux: list[list[str]] | None = None
    unit_has_visual_step: list[bool] | None = None
    bm25_core: Any = None
    bm25_script_aug: Any = None
    bm25_visual_step: Any = None
    bm25_region_aux: Any = None


class QAIndexService:
    _cache: dict[tuple[str, str, str], LoadedQaIndex] = {}
    _locks: dict[str, asyncio.Lock] = {}

    def __init__(self, config: AppConfig, openai_qa: OpenAIQAService):
        self.config = config
        self.jobs_service = JobsService(config)
        self.lecture_service = LectureService(config)
        self.openai_qa = openai_qa

    @classmethod
    def _job_lock(cls, job_id: str) -> asyncio.Lock:
        lock = cls._locks.get(job_id)
        if lock is None:
            lock = asyncio.Lock()
            cls._locks[job_id] = lock
        return lock

    @staticmethod
    def _normalize_embedding(vec: list[float]) -> list[float]:
        norm_sq = 0.0
        for v in vec:
            norm_sq += float(v) * float(v)
        if norm_sq <= 0.0:
            return [0.0 for _ in vec]
        norm = math.sqrt(norm_sq)
        return [float(v) / norm for v in vec]

    @staticmethod
    def _dot(a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            return 0.0
        return float(sum((float(x) * float(y)) for x, y in zip(a, b)))

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        if not isinstance(text, str):
            return []
        return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=True))
        tmp.replace(path)

    @staticmethod
    def _canonical_index_version(index_version: str | None) -> str:
        value = (index_version or "v1").strip().lower()
        if value == "v2":
            return "v2"
        return "v1"

    @staticmethod
    def _index_payload_version(index_version: str) -> str:
        return _INDEX_VERSION_V2 if index_version == "v2" else _INDEX_VERSION_V1

    def _index_path(self, job_dir: Path, *, index_version: str) -> Path:
        if index_version == "v2":
            return job_dir / "qa" / "explanation_index.v2.json"
        return job_dir / "qa" / "explanation_index.v1.json"

    def _source_fingerprint(self, job_dir: Path) -> dict[str, Any]:
        result_path = job_dir / "result.json"
        if not result_path.exists() or not result_path.is_file():
            raise QaIndexUnavailableError("Job result metadata is missing; QA index cannot be built.")
        st = result_path.stat()
        return {
            "source_result_mtime_ns": int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))),
            "source_result_size_bytes": int(st.st_size),
        }

    def _is_fresh(self, payload: dict[str, Any], fingerprint: dict[str, Any], *, index_version: str) -> bool:
        if payload.get("version") != self._index_payload_version(index_version):
            return False
        if payload.get("embedding_model") != self.config.qa_embed_model:
            return False
        if payload.get("tokenizer_version") != _TOKENIZER_VERSION:
            return False
        return (
            payload.get("source_result_mtime_ns") == fingerprint.get("source_result_mtime_ns")
            and payload.get("source_result_size_bytes") == fingerprint.get("source_result_size_bytes")
        )

    def _load_index_file(self, index_path: Path) -> dict[str, Any] | None:
        if not index_path.exists() or not index_path.is_file():
            return None
        try:
            payload = json.loads(index_path.read_text())
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    @staticmethod
    def _truncate_text(value: Any, limit: int) -> str:
        if not isinstance(value, str):
            return ""
        text = value.strip()
        if not text:
            return ""
        if len(text) <= limit:
            return text
        return text[:limit].rstrip()

    @staticmethod
    def _join_text_parts(parts: list[str], *, max_len: int) -> str:
        clean = [p.strip() for p in parts if isinstance(p, str) and p.strip()]
        if not clean:
            return ""
        text = " | ".join(clean)
        if len(text) <= max_len:
            return text
        return text[:max_len].rstrip()

    @staticmethod
    def _unit_order(unit: dict[str, Any]) -> tuple[int, int, str]:
        return (
            int(unit.get("slide_number") or 0),
            int(unit.get("step_number") or 0),
            str(unit.get("unit_id") or ""),
        )

    def _normalize_token_rows(self, rows: Any, *, expected: int, label: str) -> list[list[str]]:
        if not isinstance(rows, list) or len(rows) != expected:
            raise QaIndexUnavailableError(f"QA index {label} token data is missing or corrupted.")
        out: list[list[str]] = []
        for row in rows:
            if not isinstance(row, list):
                out.append([])
                continue
            out.append([str(tok).lower() for tok in row if isinstance(tok, str)])
        return out

    def _normalize_embedding_rows(self, rows: Any, *, expected: int, label: str) -> list[list[float]]:
        if not isinstance(rows, list) or len(rows) != expected:
            raise QaIndexUnavailableError(f"QA index {label} embeddings are missing or corrupted.")
        out: list[list[float]] = []
        for row in rows:
            if not isinstance(row, list):
                raise QaIndexUnavailableError(f"QA index {label} embeddings payload is invalid.")
            try:
                out.append([float(v) for v in row])
            except Exception as exc:
                raise QaIndexUnavailableError(f"QA index {label} embeddings payload is invalid.") from exc
        return out

    @staticmethod
    def _build_unit_and_region_lookups(
        units: list[dict[str, Any]],
        region_catalog: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
        unit_by_id: dict[str, dict[str, Any]] = {}
        for unit in units:
            if isinstance(unit, dict) and isinstance(unit.get("unit_id"), str):
                unit_by_id[unit["unit_id"]] = unit

        region_lookup: dict[tuple[str, str], dict[str, Any]] = {}
        for item in region_catalog:
            if not isinstance(item, dict):
                continue
            slide_id = item.get("slide_id")
            region_id = item.get("region_id")
            if not isinstance(slide_id, str) or not isinstance(region_id, str):
                continue
            region_lookup[(slide_id, region_id)] = item
        return unit_by_id, region_lookup

    def _build_runtime(self, payload: dict[str, Any]) -> LoadedQaIndex:
        if BM25Okapi is None:
            raise QaIndexUnavailableError(
                "QA lexical ranking dependency is unavailable. Install rank-bm25 to use slide QA."
            )
        version = payload.get("version")
        if version == _INDEX_VERSION_V2:
            return self._build_runtime_v2(payload)
        return self._build_runtime_v1(payload)

    def _build_runtime_v1(self, payload: dict[str, Any]) -> LoadedQaIndex:
        units = payload.get("units")
        embeddings = payload.get("embeddings")
        unit_tokens = payload.get("unit_tokens")
        slides = payload.get("slides")
        region_catalog = payload.get("region_catalog")

        if not isinstance(units, list) or not units:
            raise QaIndexUnavailableError("QA metadata unavailable for this job (no explanation units).")
        if not isinstance(slides, list):
            slides = []
        if not isinstance(region_catalog, list):
            region_catalog = []

        normalized_embeddings = self._normalize_embedding_rows(embeddings, expected=len(units), label="v1")
        normalized_tokens = self._normalize_token_rows(unit_tokens, expected=len(units), label="v1")
        bm25 = BM25Okapi(normalized_tokens)
        unit_by_id, region_lookup = self._build_unit_and_region_lookups(units, region_catalog)

        return LoadedQaIndex(
            payload=payload,
            index_version="v1",
            units=units,
            embeddings=normalized_embeddings,
            unit_tokens=normalized_tokens,
            bm25=bm25,
            region_catalog=region_catalog,
            slides=slides,
            unit_by_id=unit_by_id,
            region_lookup=region_lookup,
        )

    def _build_runtime_v2(self, payload: dict[str, Any]) -> LoadedQaIndex:
        units = payload.get("units")
        slides = payload.get("slides")
        region_catalog = payload.get("region_catalog")
        if not isinstance(units, list) or not units:
            raise QaIndexUnavailableError("QA metadata unavailable for this job (no explanation units).")
        if not isinstance(slides, list):
            slides = []
        if not isinstance(region_catalog, list):
            region_catalog = []

        unit_text_core = payload.get("unit_text_core")
        unit_text_script_aug = payload.get("unit_text_script_aug")
        unit_text_visual_step = payload.get("unit_text_visual_step")
        unit_text_region_aux = payload.get("unit_text_region_aux")
        if not isinstance(unit_text_core, list) or len(unit_text_core) != len(units):
            raise QaIndexUnavailableError("QA index v2 core text payload is missing or corrupted.")
        if not isinstance(unit_text_script_aug, list) or len(unit_text_script_aug) != len(units):
            raise QaIndexUnavailableError("QA index v2 script text payload is missing or corrupted.")
        if not isinstance(unit_text_visual_step, list) or len(unit_text_visual_step) != len(units):
            unit_text_visual_step = ["" for _ in range(len(units))]
        if not isinstance(unit_text_region_aux, list) or len(unit_text_region_aux) != len(units):
            raise QaIndexUnavailableError("QA index v2 region text payload is missing or corrupted.")

        embeddings_core = self._normalize_embedding_rows(payload.get("embeddings_core"), expected=len(units), label="v2 core")
        embeddings_script_aug = self._normalize_embedding_rows(
            payload.get("embeddings_script_aug"), expected=len(units), label="v2 script_aug"
        )
        raw_embeddings_visual_step = payload.get("embeddings_visual_step")
        if isinstance(raw_embeddings_visual_step, list):
            embeddings_visual_step = self._normalize_embedding_rows(
                raw_embeddings_visual_step,
                expected=len(units),
                label="v2 visual_step",
            )
        else:
            embeddings_visual_step = [[] for _ in range(len(units))]
        embeddings_region_aux = self._normalize_embedding_rows(
            payload.get("embeddings_region_aux"), expected=len(units), label="v2 region_aux"
        )
        unit_tokens_core = self._normalize_token_rows(payload.get("unit_tokens_core"), expected=len(units), label="v2 core")
        unit_tokens_script_aug = self._normalize_token_rows(
            payload.get("unit_tokens_script_aug"), expected=len(units), label="v2 script_aug"
        )
        raw_tokens_visual_step = payload.get("unit_tokens_visual_step")
        if isinstance(raw_tokens_visual_step, list) and len(raw_tokens_visual_step) == len(units):
            unit_tokens_visual_step = self._normalize_token_rows(
                raw_tokens_visual_step,
                expected=len(units),
                label="v2 visual_step",
            )
        else:
            unit_tokens_visual_step = [self._tokenize(str(v) if isinstance(v, str) else "") for v in unit_text_visual_step]
        unit_tokens_region_aux = self._normalize_token_rows(
            payload.get("unit_tokens_region_aux"), expected=len(units), label="v2 region_aux"
        )

        bm25_core = BM25Okapi(unit_tokens_core)
        bm25_script_aug = BM25Okapi(unit_tokens_script_aug)
        unit_has_visual_step = [bool(str(v).strip()) for v in unit_text_visual_step]
        bm25_visual_docs = (
            unit_tokens_visual_step
            if any(unit_has_visual_step)
            else [["_no_visual_step_"] for _ in range(len(units))]
        )
        bm25_visual_step = BM25Okapi(bm25_visual_docs)
        bm25_region_aux = BM25Okapi(unit_tokens_region_aux)
        unit_by_id, region_lookup = self._build_unit_and_region_lookups(units, region_catalog)

        return LoadedQaIndex(
            payload=payload,
            index_version="v2",
            units=units,
            embeddings=embeddings_core,
            unit_tokens=unit_tokens_core,
            bm25=bm25_core,
            region_catalog=region_catalog,
            slides=slides,
            unit_by_id=unit_by_id,
            region_lookup=region_lookup,
            unit_text_core=[str(v) if isinstance(v, str) else "" for v in unit_text_core],
            unit_text_script_aug=[str(v) if isinstance(v, str) else "" for v in unit_text_script_aug],
            unit_text_visual_step=[str(v) if isinstance(v, str) else "" for v in unit_text_visual_step],
            unit_text_region_aux=[str(v) if isinstance(v, str) else "" for v in unit_text_region_aux],
            embeddings_core=embeddings_core,
            embeddings_script_aug=embeddings_script_aug,
            embeddings_visual_step=embeddings_visual_step,
            embeddings_region_aux=embeddings_region_aux,
            unit_tokens_core=unit_tokens_core,
            unit_tokens_script_aug=unit_tokens_script_aug,
            unit_tokens_visual_step=unit_tokens_visual_step,
            unit_tokens_region_aux=unit_tokens_region_aux,
            unit_has_visual_step=unit_has_visual_step,
            bm25_core=bm25_core,
            bm25_script_aug=bm25_script_aug,
            bm25_visual_step=bm25_visual_step,
            bm25_region_aux=bm25_region_aux,
        )

    async def _collect_lecture_rows(
        self, job_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        lecture = self.lecture_service.get_lecture_payload(job_id, ensure_rendered_steps=False)
        if lecture is None:
            raise QaIndexUnavailableError("Job lecture metadata is unavailable.")

        raw_slides = lecture.get("slides")
        if not isinstance(raw_slides, list):
            raw_slides = []

        unit_rows: list[dict[str, Any]] = []
        slides_meta: list[dict[str, Any]] = []
        region_catalog: list[dict[str, Any]] = []
        region_seen: set[tuple[str, str]] = set()
        region_meta_by_key: dict[tuple[str, str], dict[str, Any]] = {}

        for slide in raw_slides:
            if not isinstance(slide, dict):
                continue

            slide_id = slide.get("slide_id") if isinstance(slide.get("slide_id"), str) else None
            if not slide_id:
                image_name = slide.get("image_name")
                slide_id = image_name if isinstance(image_name, str) and image_name else None
            if not slide_id:
                continue

            try:
                slide_number = int(slide.get("slide_number"))
            except Exception:
                slide_number = 0

            chunk_id = slide.get("chunk_id") if isinstance(slide.get("chunk_id"), str) else None
            if not chunk_id:
                chunk_id = "chunk_unknown"

            script_title = self._truncate_text(slide.get("script_title"), 120)
            script_summary = self._truncate_text(slide.get("script_summary"), 400)

            slides_meta.append(
                {
                    "slide_id": slide_id,
                    "slide_number": slide_number,
                    "chunk_id": chunk_id,
                }
            )

            slide_region_meta: dict[str, dict[str, Any]] = {}
            regions = slide.get("regions")
            if isinstance(regions, list):
                for region in regions:
                    if not isinstance(region, dict):
                        continue
                    region_id = region.get("id")
                    if not isinstance(region_id, str) or not region_id:
                        continue
                    kind = region.get("kind") if isinstance(region.get("kind"), str) else None
                    description = (
                        region.get("display")
                        if isinstance(region.get("display"), str) and region.get("display").strip()
                        else None
                    )
                    raw_bbox = region.get("bbox")
                    bbox: list[float] | None = None
                    if isinstance(raw_bbox, list) and len(raw_bbox) == 4:
                        try:
                            bbox = [float(v) for v in raw_bbox]
                        except Exception:
                            bbox = None
                    slide_region_meta[region_id] = {
                        "kind": kind,
                        "description": description,
                        "bbox": bbox,
                    }
                    key = (slide_id, region_id)
                    region_meta_by_key[key] = dict(slide_region_meta[region_id])
                    if key in region_seen:
                        continue
                    region_seen.add(key)
                    region_catalog.append(
                        {
                            "slide_id": slide_id,
                            "slide_number": slide_number,
                            "region_id": region_id,
                            "kind": kind,
                            "description": description,
                            "bbox": bbox,
                        }
                    )

            raw_steps = slide.get("steps")
            if not isinstance(raw_steps, list):
                continue

            normalized_steps: list[dict[str, Any]] = []
            for step in raw_steps:
                if not isinstance(step, dict):
                    continue
                line = step.get("line")
                if not isinstance(line, str) or not line.strip():
                    continue
                step_id = step.get("step_id")
                if not isinstance(step_id, str) or not step_id.strip():
                    continue
                try:
                    step_number = int(step.get("step_number"))
                except Exception:
                    step_number = 0
                raw_region_ids = step.get("region_ids")
                region_ids = (
                    [str(v) for v in raw_region_ids if isinstance(v, str)]
                    if isinstance(raw_region_ids, list)
                    else []
                )
                visual_what = step.get("what")
                if not isinstance(visual_what, str) or not visual_what.strip():
                    visual_what = ""
                visual_description = step.get("description_of_how_it_looks")
                if not isinstance(visual_description, str) or not visual_description.strip():
                    visual_description = ""
                normalized_steps.append(
                    {
                        "step_id": step_id.strip(),
                        "step_number": step_number,
                        "line": line.strip(),
                        "region_ids": region_ids,
                        "what": visual_what.strip(),
                        "description_of_how_it_looks": visual_description.strip(),
                    }
                )

            for idx, step in enumerate(normalized_steps):
                line = step["line"]
                prev_line = normalized_steps[idx - 1]["line"] if idx - 1 >= 0 else ""
                next_line = normalized_steps[idx + 1]["line"] if idx + 1 < len(normalized_steps) else ""
                region_ids = step["region_ids"]

                region_desc_parts: list[str] = []
                region_kind_parts: list[str] = []
                for region_id in region_ids:
                    meta = slide_region_meta.get(region_id) or {}
                    kind = self._truncate_text(meta.get("kind"), 60)
                    desc = self._truncate_text(meta.get("description"), 160)
                    if desc:
                        region_desc_parts.append(desc)
                    if kind:
                        region_kind_parts.append(kind)

                region_desc_parts = region_desc_parts[:3]
                region_kind_parts = region_kind_parts[:3]

                unit_id = f"{chunk_id}:{slide_id}:{step['step_id']}"
                unit = {
                    "unit_id": unit_id,
                    "chunk_id": chunk_id,
                    "slide_id": slide_id,
                    "slide_number": slide_number,
                    "step_id": step["step_id"],
                    "step_number": step["step_number"],
                    "explanation_text": line,
                    "region_ids": list(region_ids),
                }

                unit_text_core = line
                unit_text_script_aug = self._join_text_parts(
                    [
                        line,
                        script_title,
                        script_summary,
                        self._truncate_text(prev_line, 300),
                        self._truncate_text(next_line, 300),
                    ],
                    max_len=1400,
                )
                unit_text_region_aux = self._join_text_parts(
                    [
                        line,
                        *region_desc_parts,
                        *region_kind_parts,
                    ],
                    max_len=1200,
                )
                unit_text_visual_step = self._join_text_parts(
                    [
                        step.get("what") if isinstance(step.get("what"), str) else "",
                        step.get("description_of_how_it_looks")
                        if isinstance(step.get("description_of_how_it_looks"), str)
                        else "",
                    ],
                    max_len=1200,
                )

                unit_rows.append(
                    {
                        "unit": unit,
                        "unit_text_core": unit_text_core,
                        "unit_text_script_aug": unit_text_script_aug or unit_text_core,
                        "unit_text_visual_step": unit_text_visual_step,
                        "unit_text_region_aux": unit_text_region_aux or unit_text_core,
                    }
                )

        if not unit_rows:
            raise QaIndexUnavailableError(
                "QA metadata unavailable for this job. Explanation units could not be built."
            )

        # Ensure referenced IDs exist in the region catalog, even if no description is present.
        for row in unit_rows:
            unit = row["unit"]
            slide_id = unit["slide_id"]
            slide_number = int(unit.get("slide_number") or 0)
            for region_id in unit.get("region_ids", []):
                if not isinstance(region_id, str):
                    continue
                key = (slide_id, region_id)
                if key in region_seen:
                    continue
                region_seen.add(key)
                meta = region_meta_by_key.get(key) or {}
                region_catalog.append(
                    {
                        "slide_id": slide_id,
                        "slide_number": slide_number,
                        "region_id": region_id,
                        "kind": meta.get("kind") if isinstance(meta.get("kind"), str) else None,
                        "description": (
                            meta.get("description")
                            if isinstance(meta.get("description"), str) and meta.get("description").strip()
                            else None
                        ),
                        "bbox": meta.get("bbox") if isinstance(meta.get("bbox"), list) else None,
                    }
                )

        unit_rows.sort(key=lambda row: self._unit_order(row["unit"]))
        slides_meta.sort(key=lambda s: (int(s.get("slide_number") or 0), str(s.get("slide_id") or "")))
        region_catalog.sort(
            key=lambda r: (
                int(r.get("slide_number") or 0),
                str(r.get("slide_id") or ""),
                str(r.get("region_id") or ""),
            )
        )
        return unit_rows, slides_meta, region_catalog

    async def _build_index_from_lecture_v1(self, job_id: str, *, fingerprint: dict[str, Any]) -> dict[str, Any]:
        unit_rows, slides_meta, region_catalog = await self._collect_lecture_rows(job_id)
        units = [row["unit"] for row in unit_rows]
        unit_texts = [row["unit_text_core"] for row in unit_rows]
        unit_tokens = [self._tokenize(text) for text in unit_texts]
        embeddings_raw = await self.openai_qa.embed_texts(unit_texts)
        if len(embeddings_raw) != len(units):
            raise QaIndexUnavailableError("Failed to generate embeddings for explanation units.")
        embeddings = [self._normalize_embedding(vec) for vec in embeddings_raw]

        payload: dict[str, Any] = {
            "version": _INDEX_VERSION_V1,
            "job_id": job_id,
            "embedding_model": self.config.qa_embed_model,
            "tokenizer_version": _TOKENIZER_VERSION,
            **fingerprint,
            "units": units,
            "embeddings": embeddings,
            "unit_tokens": unit_tokens,
            "slides": slides_meta,
            "region_catalog": region_catalog,
        }
        return payload

    async def _build_index_from_lecture_v2(self, job_id: str, *, fingerprint: dict[str, Any]) -> dict[str, Any]:
        unit_rows, slides_meta, region_catalog = await self._collect_lecture_rows(job_id)
        units = [row["unit"] for row in unit_rows]

        unit_text_core = [row["unit_text_core"] for row in unit_rows]
        unit_text_script_aug = [row["unit_text_script_aug"] for row in unit_rows]
        unit_text_visual_step = [row["unit_text_visual_step"] for row in unit_rows]
        unit_text_region_aux = [row["unit_text_region_aux"] for row in unit_rows]

        unit_tokens_core = [self._tokenize(text) for text in unit_text_core]
        unit_tokens_script_aug = [self._tokenize(text) for text in unit_text_script_aug]
        unit_tokens_visual_step = [self._tokenize(text) for text in unit_text_visual_step]
        unit_tokens_region_aux = [self._tokenize(text) for text in unit_text_region_aux]

        emb_core_raw = await self.openai_qa.embed_texts(unit_text_core)
        emb_script_raw = await self.openai_qa.embed_texts(unit_text_script_aug)
        visual_indices = [idx for idx, text in enumerate(unit_text_visual_step) if isinstance(text, str) and text.strip()]
        emb_visual_raw: list[list[float]] = []
        if visual_indices:
            emb_visual_raw = await self.openai_qa.embed_texts([unit_text_visual_step[idx] for idx in visual_indices])
        emb_region_raw = await self.openai_qa.embed_texts(unit_text_region_aux)
        if not (
            len(emb_core_raw) == len(units)
            and len(emb_script_raw) == len(units)
            and len(emb_visual_raw) == len(visual_indices)
            and len(emb_region_raw) == len(units)
        ):
            raise QaIndexUnavailableError("Failed to generate embeddings for QA index v2 fields.")

        embeddings_core = [self._normalize_embedding(vec) for vec in emb_core_raw]
        embeddings_script_aug = [self._normalize_embedding(vec) for vec in emb_script_raw]
        embeddings_visual_step: list[list[float]] = [[] for _ in range(len(units))]
        for pos, idx in enumerate(visual_indices):
            embeddings_visual_step[idx] = self._normalize_embedding(emb_visual_raw[pos])
        embeddings_region_aux = [self._normalize_embedding(vec) for vec in emb_region_raw]

        payload: dict[str, Any] = {
            "version": _INDEX_VERSION_V2,
            "job_id": job_id,
            "embedding_model": self.config.qa_embed_model,
            "tokenizer_version": _TOKENIZER_VERSION,
            **fingerprint,
            "units": units,
            # Keep legacy aliases pointing to core view for easier backward compatibility.
            "embeddings": embeddings_core,
            "unit_tokens": unit_tokens_core,
            "slides": slides_meta,
            "region_catalog": region_catalog,
            "unit_text_core": unit_text_core,
            "unit_text_script_aug": unit_text_script_aug,
            "unit_text_visual_step": unit_text_visual_step,
            "unit_text_region_aux": unit_text_region_aux,
            "embeddings_core": embeddings_core,
            "embeddings_script_aug": embeddings_script_aug,
            "embeddings_visual_step": embeddings_visual_step,
            "embeddings_region_aux": embeddings_region_aux,
            "unit_tokens_core": unit_tokens_core,
            "unit_tokens_script_aug": unit_tokens_script_aug,
            "unit_tokens_visual_step": unit_tokens_visual_step,
            "unit_tokens_region_aux": unit_tokens_region_aux,
        }
        return payload

    async def load_or_build_index(self, job_id: str, *, index_version: str = "v1") -> LoadedQaIndex:
        index_version = self._canonical_index_version(index_version)
        job_dir = self.jobs_service.resolve_job_dir(job_id)
        if job_dir is None:
            raise FileNotFoundError(f"Job {job_id} not found.")

        fingerprint = self._source_fingerprint(job_dir)
        cache_key = (job_id, self.config.qa_embed_model, index_version)
        cached = self._cache.get(cache_key)
        if cached is not None and self._is_fresh(cached.payload, fingerprint, index_version=index_version):
            logger.info("qa.index_cache_hit job_id=%s version=%s", job_id, index_version)
            return cached

        lock = self._job_lock(job_id)
        async with lock:
            cached = self._cache.get(cache_key)
            if cached is not None and self._is_fresh(cached.payload, fingerprint, index_version=index_version):
                logger.info("qa.index_cache_hit job_id=%s version=%s", job_id, index_version)
                return cached

            index_path = self._index_path(job_dir, index_version=index_version)
            disk_payload = self._load_index_file(index_path)
            if disk_payload is not None and self._is_fresh(disk_payload, fingerprint, index_version=index_version):
                runtime = self._build_runtime(disk_payload)
                self._cache[cache_key] = runtime
                logger.info("qa.index_cache_miss job_id=%s version=%s source=disk", job_id, index_version)
                return runtime

            logger.info("qa.index_rebuild job_id=%s version=%s", job_id, index_version)
            if index_version == "v2":
                payload = await self._build_index_from_lecture_v2(job_id, fingerprint=fingerprint)
            else:
                payload = await self._build_index_from_lecture_v1(job_id, fingerprint=fingerprint)
            self._atomic_write_json(index_path, payload)
            runtime = self._build_runtime(payload)
            self._cache[cache_key] = runtime
            return runtime

    def rank_candidates(
        self,
        *,
        index: LoadedQaIndex,
        question_embedding: list[float],
        question_text: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        if not index.units:
            return []

        q_emb = self._normalize_embedding(question_embedding)
        semantic_scores = [self._dot(q_emb, emb) for emb in index.embeddings]

        q_tokens = self._tokenize(question_text)
        if q_tokens:
            raw_bm25_scores = index.bm25.get_scores(q_tokens)
            try:
                bm25_scores = [float(v) for v in raw_bm25_scores]
            except Exception:
                bm25_scores = [0.0 for _ in index.units]
        else:
            bm25_scores = [0.0 for _ in index.units]

        def unit_order(i: int) -> tuple[int, int, str]:
            return self._unit_order(index.units[i])

        semantic_sorted = sorted(range(len(index.units)), key=lambda i: (-semantic_scores[i], *unit_order(i)))
        bm25_sorted = sorted(range(len(index.units)), key=lambda i: (-bm25_scores[i], *unit_order(i)))

        semantic_rank = {idx: rank for rank, idx in enumerate(semantic_sorted, start=1)}
        bm25_rank = {idx: rank for rank, idx in enumerate(bm25_sorted, start=1)}

        merged = []
        for idx, unit in enumerate(index.units):
            s_rank = semantic_rank[idx]
            b_rank = bm25_rank[idx]
            rrf = (1.0 / (_RRF_K + s_rank)) + (1.0 / (_RRF_K + b_rank))
            merged.append(
                {
                    "unit": unit,
                    "semantic_similarity": float(semantic_scores[idx]),
                    "semantic_rank": int(s_rank),
                    "bm25_score": float(bm25_scores[idx]),
                    "bm25_rank": int(b_rank),
                    "rrf_score": float(rrf),
                }
            )

        merged.sort(
            key=lambda item: (
                -float(item["rrf_score"]),
                -float(item["semantic_similarity"]),
                -float(item["bm25_score"]),
                *self._unit_order(item["unit"]),
            )
        )
        return merged[: max(1, int(top_k))]

    @staticmethod
    def _scores_to_float_list(raw_scores: Any, size: int) -> list[float]:
        try:
            out = [float(v) for v in raw_scores]
        except Exception:
            return [0.0 for _ in range(size)]
        if len(out) != size:
            return [0.0 for _ in range(size)]
        return out

    def _rank_map_from_scores(self, scores: list[float], units: list[dict[str, Any]]) -> dict[int, int]:
        order = sorted(
            range(len(units)),
            key=lambda i: (-float(scores[i]), *self._unit_order(units[i])),
        )
        return {idx: rank for rank, idx in enumerate(order, start=1)}

    def _rank_map_from_scores_masked(
        self,
        scores: list[float],
        units: list[dict[str, Any]],
        include_mask: list[bool],
    ) -> dict[int, int]:
        order = [
            i
            for i in range(len(units))
            if i < len(include_mask) and bool(include_mask[i])
        ]
        order.sort(key=lambda i: (-float(scores[i]), *self._unit_order(units[i])))
        return {idx: rank for rank, idx in enumerate(order, start=1)}

    def rank_candidates_v2(
        self,
        *,
        index: LoadedQaIndex,
        query_variants: list[str],
        query_embeddings: list[list[float]],
        top_k_per_query: int,
        merged_cap: int,
    ) -> list[dict[str, Any]]:
        if not index.units:
            return []
        if index.index_version != "v2":
            raise QaIndexUnavailableError("QA index v2 ranking requires a v2 index.")
        if not query_variants or not query_embeddings or len(query_variants) != len(query_embeddings):
            return []

        emb_core = index.embeddings_core
        emb_script = index.embeddings_script_aug
        emb_visual = index.embeddings_visual_step
        emb_region = index.embeddings_region_aux
        bm25_core = index.bm25_core
        bm25_script = index.bm25_script_aug
        bm25_visual = index.bm25_visual_step
        bm25_region = index.bm25_region_aux
        has_visual = index.unit_has_visual_step
        if (
            emb_core is None
            or emb_script is None
            or emb_visual is None
            or emb_region is None
            or bm25_core is None
            or bm25_script is None
            or bm25_visual is None
            or bm25_region is None
            or has_visual is None
        ):
            raise QaIndexUnavailableError("QA index v2 runtime is incomplete.")

        n = len(index.units)
        has_any_visual = any(bool(v) for v in has_visual)
        per_variant_rank_maps: list[dict[int, int]] = []
        per_variant_weights: list[float] = []
        per_variant_top_sets: list[set[int]] = []
        per_variant_texts: list[str] = []

        original_dense_core_scores: list[float] = [0.0 for _ in range(n)]
        original_bm25_core_scores: list[float] = [0.0 for _ in range(n)]
        original_dense_script_scores: list[float] = [0.0 for _ in range(n)]
        original_bm25_script_scores: list[float] = [0.0 for _ in range(n)]
        original_dense_visual_scores: list[float] = [0.0 for _ in range(n)]
        original_bm25_visual_scores: list[float] = [0.0 for _ in range(n)]
        original_dense_region_scores: list[float] = [0.0 for _ in range(n)]
        original_bm25_region_scores: list[float] = [0.0 for _ in range(n)]

        q_cap = max(1, int(top_k_per_query))
        for q_idx, (q_text, q_emb_raw) in enumerate(zip(query_variants, query_embeddings)):
            q_text_norm = q_text.strip()
            q_emb = self._normalize_embedding(q_emb_raw)
            q_tokens = self._tokenize(q_text_norm)

            dense_core_scores = [self._dot(q_emb, emb) for emb in emb_core]
            dense_script_scores = [self._dot(q_emb, emb) for emb in emb_script]
            dense_visual_scores = [
                self._dot(q_emb, emb_visual[i]) if i < len(has_visual) and bool(has_visual[i]) else 0.0
                for i in range(n)
            ]
            dense_region_scores = [self._dot(q_emb, emb) for emb in emb_region]

            if q_tokens:
                bm25_core_scores = self._scores_to_float_list(bm25_core.get_scores(q_tokens), n)
                bm25_script_scores = self._scores_to_float_list(bm25_script.get_scores(q_tokens), n)
                if has_any_visual:
                    bm25_visual_scores = self._scores_to_float_list(bm25_visual.get_scores(q_tokens), n)
                    bm25_visual_scores = [
                        float(score) if i < len(has_visual) and bool(has_visual[i]) else 0.0
                        for i, score in enumerate(bm25_visual_scores)
                    ]
                else:
                    bm25_visual_scores = [0.0 for _ in range(n)]
                bm25_region_scores = self._scores_to_float_list(bm25_region.get_scores(q_tokens), n)
            else:
                bm25_core_scores = [0.0 for _ in range(n)]
                bm25_script_scores = [0.0 for _ in range(n)]
                bm25_visual_scores = [0.0 for _ in range(n)]
                bm25_region_scores = [0.0 for _ in range(n)]

            if q_idx == 0:
                original_dense_core_scores = dense_core_scores
                original_bm25_core_scores = bm25_core_scores
                original_dense_script_scores = dense_script_scores
                original_bm25_script_scores = bm25_script_scores
                original_dense_visual_scores = dense_visual_scores
                original_bm25_visual_scores = bm25_visual_scores
                original_dense_region_scores = dense_region_scores
                original_bm25_region_scores = bm25_region_scores

            rank_dense_core = self._rank_map_from_scores(dense_core_scores, index.units)
            rank_bm25_core = self._rank_map_from_scores(bm25_core_scores, index.units)
            rank_dense_script = self._rank_map_from_scores(dense_script_scores, index.units)
            rank_bm25_script = self._rank_map_from_scores(bm25_script_scores, index.units)
            rank_dense_visual = self._rank_map_from_scores_masked(
                dense_visual_scores,
                index.units,
                has_visual,
            )
            rank_bm25_visual = self._rank_map_from_scores_masked(
                bm25_visual_scores,
                index.units,
                has_visual,
            )
            rank_dense_region = self._rank_map_from_scores(dense_region_scores, index.units)
            rank_bm25_region = self._rank_map_from_scores(bm25_region_scores, index.units)

            query_scores: list[float] = [0.0 for _ in range(n)]
            for i in range(n):
                score = (
                    _V2_FIELD_WEIGHTS["dense_core"] / (_RRF_K + rank_dense_core[i])
                    + _V2_FIELD_WEIGHTS["bm25_core"] / (_RRF_K + rank_bm25_core[i])
                    + _V2_FIELD_WEIGHTS["dense_script_aug"] / (_RRF_K + rank_dense_script[i])
                    + _V2_FIELD_WEIGHTS["bm25_script_aug"] / (_RRF_K + rank_bm25_script[i])
                    + _V2_FIELD_WEIGHTS["dense_region_aux"] / (_RRF_K + rank_dense_region[i])
                    + _V2_FIELD_WEIGHTS["bm25_region_aux"] / (_RRF_K + rank_bm25_region[i])
                )
                dense_visual_rank = rank_dense_visual.get(i)
                if dense_visual_rank is not None:
                    score += _V2_FIELD_WEIGHTS["dense_visual_step"] / (_RRF_K + dense_visual_rank)
                bm25_visual_rank = rank_bm25_visual.get(i)
                if bm25_visual_rank is not None:
                    score += _V2_FIELD_WEIGHTS["bm25_visual_step"] / (_RRF_K + bm25_visual_rank)
                query_scores[i] = score

            query_sorted = sorted(
                range(n),
                key=lambda i: (-query_scores[i], *self._unit_order(index.units[i])),
            )
            query_rank_map = {idx: rank for rank, idx in enumerate(query_sorted, start=1)}
            per_variant_rank_maps.append(query_rank_map)
            per_variant_weights.append(_V2_QUERY_WEIGHT_ORIGINAL if q_idx == 0 else _V2_QUERY_WEIGHT_REWRITE)
            per_variant_top_sets.append(set(query_sorted[:q_cap]))
            per_variant_texts.append(q_text_norm)

        merged: list[dict[str, Any]] = []
        for i, unit in enumerate(index.units):
            merged_score = 0.0
            query_hits: list[str] = []
            for q_idx, rank_map in enumerate(per_variant_rank_maps):
                rank = rank_map.get(i)
                if rank is None:
                    continue
                merged_score += per_variant_weights[q_idx] / (_RRF_K + rank)
                if i in per_variant_top_sets[q_idx]:
                    query_hits.append(per_variant_texts[q_idx])
            merged.append(
                {
                    "unit": unit,
                    "rrf_score": float(merged_score),
                    "query_contrib_score": float(merged_score),
                    "dense_core": float(original_dense_core_scores[i]),
                    "bm25_core": float(original_bm25_core_scores[i]),
                    "dense_script_aug": float(original_dense_script_scores[i]),
                    "bm25_script_aug": float(original_bm25_script_scores[i]),
                    "dense_visual_step": float(original_dense_visual_scores[i]),
                    "bm25_visual_step": float(original_bm25_visual_scores[i]),
                    "dense_region_aux": float(original_dense_region_scores[i]),
                    "bm25_region_aux": float(original_bm25_region_scores[i]),
                    "has_visual_step": bool(has_visual[i]) if i < len(has_visual) else False,
                    "query_hits": query_hits,
                    # compatibility aliases used by some debug codepaths
                    "semantic_similarity": float(original_dense_core_scores[i]),
                    "bm25_score": float(original_bm25_core_scores[i]),
                }
            )

        merged.sort(
            key=lambda item: (
                -float(item["rrf_score"]),
                -float(item["dense_core"]),
                -float(item["bm25_core"]),
                *self._unit_order(item["unit"]),
            )
        )
        cap = max(1, int(merged_cap))
        return merged[:cap]
