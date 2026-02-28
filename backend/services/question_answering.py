from __future__ import annotations

import hashlib
import inspect
import logging
import re
import time
from difflib import SequenceMatcher
from typing import Any, Callable, Optional

from fastapi import HTTPException

from ..config import AppConfig
from ..schemas import QaAnswerRequest
from .jobs import JobsService
from .openai_qa import OpenAIJSONError, OpenAIQAError, OpenAIQATimeoutError, OpenAIQAService
from .qa_index import QAIndexService, QaIndexUnavailableError


logger = logging.getLogger(__name__)


class QuestionAnsweringService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.jobs_service = JobsService(config)

    @staticmethod
    def _ms(start: float) -> int:
        return int(round((time.perf_counter() - start) * 1000))

    @staticmethod
    def _stable_question_hash(question: str) -> str:
        return hashlib.sha1(question.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    async def _emit_progress(
        progress_cb: Optional[Callable[[dict[str, Any]], Any]],
        *,
        stage: str,
        message: str,
        **extra: Any,
    ) -> None:
        if progress_cb is None:
            return
        payload: dict[str, Any] = {"stage": stage, "message": message}
        payload.update(extra)
        try:
            maybe = progress_cb(payload)
            if inspect.isawaitable(maybe):
                await maybe
        except Exception:
            logger.debug("qa.progress_callback_failed stage=%s", stage, exc_info=True)

    @staticmethod
    def _candidate_card_from_unit(unit: dict[str, Any]) -> dict[str, Any]:
        return {
            "unit_id": unit.get("unit_id"),
            "chunk_id": unit.get("chunk_id"),
            "slide_id": unit.get("slide_id"),
            "slide_number": unit.get("slide_number"),
            "step_id": unit.get("step_id"),
            "step_number": unit.get("step_number"),
            "explanation_text": unit.get("explanation_text"),
            "region_ids": unit.get("region_ids") if isinstance(unit.get("region_ids"), list) else [],
        }

    @staticmethod
    def _unique_in_order(values: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                continue
            if value in seen:
                continue
            seen.add(value)
            out.append(value)
        return out

    @staticmethod
    def _expand_context_units(
        *,
        selected_unit_ids: list[str],
        all_units: list[dict[str, Any]],
        max_expanded_units: int = 12,
    ) -> list[str]:
        by_id = {
            str(unit.get("unit_id")): unit
            for unit in all_units
            if isinstance(unit, dict) and isinstance(unit.get("unit_id"), str)
        }

        slide_to_units: dict[str, list[str]] = {}
        for unit in all_units:
            if not isinstance(unit, dict):
                continue
            slide_id = unit.get("slide_id")
            unit_id = unit.get("unit_id")
            if not isinstance(slide_id, str) or not isinstance(unit_id, str):
                continue
            slide_to_units.setdefault(slide_id, []).append(unit_id)

        expanded: set[str] = set()
        selected_clean = [uid for uid in selected_unit_ids if uid in by_id]
        for uid in selected_clean:
            expanded.add(uid)
            unit = by_id[uid]
            slide_id = unit.get("slide_id")
            if not isinstance(slide_id, str):
                continue
            sequence = slide_to_units.get(slide_id, [])
            try:
                idx = sequence.index(uid)
            except ValueError:
                continue
            if idx - 1 >= 0:
                expanded.add(sequence[idx - 1])
            if idx + 1 < len(sequence):
                expanded.add(sequence[idx + 1])

        ordered_all = [
            str(unit.get("unit_id"))
            for unit in all_units
            if isinstance(unit, dict) and isinstance(unit.get("unit_id"), str)
        ]
        ordered_expanded = [uid for uid in ordered_all if uid in expanded]

        if len(ordered_expanded) <= max_expanded_units:
            return ordered_expanded

        selected_set = set(selected_clean)
        must_keep = [uid for uid in ordered_expanded if uid in selected_set]
        if len(must_keep) >= max_expanded_units:
            return must_keep[:max_expanded_units]

        out = list(must_keep)
        for uid in ordered_expanded:
            if uid in selected_set:
                continue
            out.append(uid)
            if len(out) >= max_expanded_units:
                break
        return out

    @staticmethod
    def _build_region_catalog_for_context(
        *,
        region_catalog_all: list[dict[str, Any]],
        context_units: list[dict[str, Any]],
        only_referenced_regions: bool = False,
        extra_visual_regions_per_slide: int = 0,
        extra_text_regions_per_slide: int = 0,
        max_total_regions: Optional[int] = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        context_slide_ids = {
            str(unit.get("slide_id"))
            for unit in context_units
            if isinstance(unit, dict) and isinstance(unit.get("slide_id"), str)
        }
        referenced_region_keys: set[tuple[str, str]] = set()
        if only_referenced_regions:
            for unit in context_units:
                if not isinstance(unit, dict):
                    continue
                slide_id = unit.get("slide_id")
                region_ids = unit.get("region_ids")
                if not isinstance(slide_id, str) or not isinstance(region_ids, list):
                    continue
                for region_id in region_ids:
                    if isinstance(region_id, str) and region_id:
                        referenced_region_keys.add((slide_id, region_id))

        normalized_items: list[dict[str, Any]] = []
        slide_numbers: dict[str, int] = {}
        for item in region_catalog_all:
            if not isinstance(item, dict):
                continue
            slide_id = item.get("slide_id")
            region_id = item.get("region_id")
            if not isinstance(slide_id, str) or slide_id not in context_slide_ids:
                continue
            if not isinstance(region_id, str) or not region_id:
                continue
            kind_raw = str(item.get("kind") or "").strip().lower()
            if kind_raw not in {"visual", "text"}:
                # Keep canonical visual/text IDs even when region kind metadata is absent.
                if region_id.startswith("v:"):
                    kind_raw = "visual"
                elif region_id.startswith("t:"):
                    kind_raw = "text"
                else:
                    # Exclude composite/unknown regions (e.g., c:*) from QA highlight selection.
                    continue
            slide_no = int(item.get("slide_number") or 0)
            raw_bbox = item.get("bbox")
            bbox: list[float] | None = None
            if isinstance(raw_bbox, list) and len(raw_bbox) == 4:
                try:
                    bbox = [float(v) for v in raw_bbox]
                except Exception:
                    bbox = None
            slide_numbers[slide_id] = slide_no
            normalized_items.append(
                {
                    "slide_id": slide_id,
                    "slide_number": slide_no,
                    "region_id": region_id,
                    "kind": kind_raw,
                    "bbox": bbox,
                    "description": (
                        item.get("description")
                        if isinstance(item.get("description"), str) and item.get("description").strip()
                        else None
                    ),
                }
            )

        out: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        slide_counts: dict[str, int] = {}
        extra_regions_added = 0

        def _append_region(item: dict[str, Any]) -> bool:
            nonlocal out
            if max_total_regions is not None and len(out) >= max(0, int(max_total_regions)):
                return False
            slide_id = item.get("slide_id")
            region_id = item.get("region_id")
            if not isinstance(slide_id, str) or not isinstance(region_id, str):
                return False
            key = (slide_id, region_id)
            if key in seen:
                return False
            seen.add(key)
            slide_counts[slide_id] = slide_counts.get(slide_id, 0) + 1
            slide_numbers.setdefault(slide_id, int(item.get("slide_number") or 0))
            out.append(
                {
                    "slide_id": slide_id,
                    "slide_number": int(item.get("slide_number") or 0),
                    "region_id": region_id,
                    "kind": item.get("kind") if isinstance(item.get("kind"), str) else None,
                    "bbox": item.get("bbox") if isinstance(item.get("bbox"), list) else None,
                    "description": (
                        item.get("description")
                        if isinstance(item.get("description"), str) and item.get("description").strip()
                        else None
                    ),
                }
            )
            return True

        # Pass 1: include referenced regions (v2) or all context-slide regions (v1 behavior).
        for item in normalized_items:
            key = (str(item.get("slide_id") or ""), str(item.get("region_id") or ""))
            if only_referenced_regions and key not in referenced_region_keys:
                continue
            _append_region(item)

        # Pass 2 (v2): add a bounded set of extra regions per slide to improve visual grounding coverage.
        if only_referenced_regions and (extra_visual_regions_per_slide > 0 or extra_text_regions_per_slide > 0):
            slide_extra_visual_counts: dict[str, int] = {}
            slide_extra_text_counts: dict[str, int] = {}
            extras_sorted = sorted(
                normalized_items,
                key=lambda item: (
                    int(item.get("slide_number") or 0),
                    str(item.get("slide_id") or ""),
                    0 if str(item.get("kind") or "").lower() == "visual" else 1,
                    0
                    if (
                        isinstance(item.get("description"), str)
                        and item["description"].strip()
                        and item["description"].strip().lower() not in {"visual", "text"}
                    )
                    else 1,
                    str(item.get("region_id") or ""),
                ),
            )
            for item in extras_sorted:
                slide_id = item.get("slide_id")
                region_id = item.get("region_id")
                if not isinstance(slide_id, str) or not isinstance(region_id, str):
                    continue
                key = (slide_id, region_id)
                if key in seen:
                    continue
                kind = str(item.get("kind") or "").lower()
                if kind == "visual":
                    if slide_extra_visual_counts.get(slide_id, 0) >= max(0, int(extra_visual_regions_per_slide)):
                        continue
                    if _append_region(item):
                        slide_extra_visual_counts[slide_id] = slide_extra_visual_counts.get(slide_id, 0) + 1
                        extra_regions_added += 1
                elif kind == "text":
                    if slide_extra_text_counts.get(slide_id, 0) >= max(0, int(extra_text_regions_per_slide)):
                        continue
                    if _append_region(item):
                        slide_extra_text_counts[slide_id] = slide_extra_text_counts.get(slide_id, 0) + 1
                        extra_regions_added += 1
                else:
                    # Unknown kinds are treated as text-like and kept very limited.
                    if slide_extra_text_counts.get(slide_id, 0) >= max(0, int(extra_text_regions_per_slide)):
                        continue
                    if _append_region(item):
                        slide_extra_text_counts[slide_id] = slide_extra_text_counts.get(slide_id, 0) + 1
                        extra_regions_added += 1

        placeholders_added = 0

        out.sort(
            key=lambda r: (
                int(r.get("slide_number") or 0),
                str(r.get("slide_id") or ""),
                0 if str(r.get("kind") or "").lower() == "visual" else 1,
                str(r.get("region_id") or ""),
            )
        )
        context_slide_ids_ordered = [
            slide_id
            for slide_id, _ in sorted(slide_numbers.items(), key=lambda item: (item[1], item[0]))
        ]
        summary = {
            "context_slide_ids": context_slide_ids_ordered,
            "slides": [
                {
                    "slide_id": slide_id,
                    "slide_number": slide_numbers.get(slide_id, 0),
                    "region_count": slide_counts.get(slide_id, 0),
                }
                for slide_id in context_slide_ids_ordered
            ],
            "total_regions": len(out),
            "placeholders_added": placeholders_added,
            "extra_regions_added": extra_regions_added,
            "only_referenced_regions": bool(only_referenced_regions),
        }
        return out, summary

    @staticmethod
    def _validate_answer_lines(
        *,
        raw_answer: dict[str, Any],
        context_units: list[dict[str, Any]],
        region_catalog: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        raw_lines = raw_answer.get("answer_lines")
        if not isinstance(raw_lines, list):
            raise OpenAIJSONError("LLM answer JSON missing answer_lines list.")

        context_unit_ids = {
            str(unit.get("unit_id"))
            for unit in context_units
            if isinstance(unit, dict) and isinstance(unit.get("unit_id"), str)
        }
        context_unit_lookup = {
            str(unit.get("unit_id")): unit
            for unit in context_units
            if isinstance(unit, dict) and isinstance(unit.get("unit_id"), str)
        }
        region_lookup: dict[tuple[str, str], dict[str, Any]] = {}
        for item in region_catalog:
            if not isinstance(item, dict):
                continue
            slide_id = item.get("slide_id")
            region_id = item.get("region_id")
            if not isinstance(slide_id, str) or not isinstance(region_id, str):
                continue
            region_lookup[(slide_id, region_id)] = item

        out: list[dict[str, Any]] = []
        def _tokens(value: str) -> set[str]:
            cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in value)
            return {tok for tok in cleaned.split() if tok and len(tok) >= 3}

        def _fuzzy_overlap(left_tokens: set[str], right_tokens: set[str]) -> int:
            if not left_tokens or not right_tokens:
                return 0
            hits = 0
            for left in left_tokens:
                matched = False
                for right in right_tokens:
                    if left == right:
                        matched = True
                        break
                    if abs(len(left) - len(right)) > 3:
                        continue
                    if SequenceMatcher(None, left, right).ratio() >= 0.84:
                        matched = True
                        break
                if matched:
                    hits += 1
            return hits

        def _bbox_center_for_key(key: tuple[str, str]) -> tuple[float, float] | None:
            region = region_lookup.get(key)
            if not isinstance(region, dict):
                return None
            raw_bbox = region.get("bbox")
            if not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
                return None
            try:
                x1, y1, x2, y2 = [float(v) for v in raw_bbox]
            except Exception:
                return None
            return ((x1 + x2) * 0.5, (y1 + y2) * 0.5)

        def _region_text_for_visual(region: dict[str, Any], key: tuple[str, str]) -> str:
            parts: list[str] = []
            desc = region.get("description")
            if isinstance(desc, str) and desc.strip():
                parts.append(desc.strip())
            label = region.get("label")
            if isinstance(label, str) and label.strip():
                parts.append(label.strip())
            rid = key[1]
            if isinstance(rid, str) and ":" in rid:
                suffix = rid.split(":", 1)[1].strip()
                if suffix:
                    parts.append(suffix.replace("_", " ").replace("-", " "))
            return " ".join(parts)

        def _rank_visual_keys(
            *,
            line_text: str,
            candidate_keys: list[tuple[str, str]],
            anchor_text_keys: list[tuple[str, str]],
        ) -> list[tuple[tuple[str, str], bool]]:
            if not candidate_keys:
                return []

            line_tokens = _tokens(line_text)
            anchor_centers = [center for key in anchor_text_keys if (center := _bbox_center_for_key(key)) is not None]
            anchor_numeric_ids: list[int] = []
            for key in anchor_text_keys:
                rid = key[1]
                if not isinstance(rid, str) or not rid.startswith("t:"):
                    continue
                try:
                    anchor_numeric_ids.append(int(rid.split(":", 1)[1]))
                except Exception:
                    continue

            scored: list[tuple[int, int, int, float, int, int, int, int, str, str, tuple[str, str]]] = []
            for key in candidate_keys:
                region = region_lookup.get(key)
                if not isinstance(region, dict):
                    continue
                cand_tokens = _tokens(_region_text_for_visual(region, key))
                overlap_exact = len(line_tokens & cand_tokens) if line_tokens and cand_tokens else 0
                overlap_fuzzy = _fuzzy_overlap(line_tokens, cand_tokens)

                center = _bbox_center_for_key(key)
                min_distance = float("inf")
                has_anchor_distance = 0
                if center is not None and anchor_centers:
                    has_anchor_distance = 1
                    for anchor_center in anchor_centers:
                        dx = center[0] - anchor_center[0]
                        dy = center[1] - anchor_center[1]
                        dist = (dx * dx + dy * dy) ** 0.5
                        if dist < min_distance:
                            min_distance = dist

                candidate_numeric = None
                try:
                    candidate_numeric = int(str(key[1]).split(":", 1)[1])
                except Exception:
                    candidate_numeric = None
                has_numeric_distance = 0
                min_numeric_distance = 10**9
                if candidate_numeric is not None and anchor_numeric_ids:
                    has_numeric_distance = 1
                    min_numeric_distance = min(abs(candidate_numeric - anchor_no) for anchor_no in anchor_numeric_ids)

                scored.append(
                    (
                        overlap_exact,
                        overlap_fuzzy,
                        has_anchor_distance,
                        min_distance if has_anchor_distance else float("inf"),
                        has_numeric_distance,
                        min_numeric_distance,
                        len(cand_tokens),
                        int(region.get("slide_number") or 0),
                        key[0],
                        key[1],
                        key,
                    )
                )

            if not scored:
                return []

            scored.sort(
                key=lambda item: (
                    -item[0],
                    -item[1],
                    -item[2],
                    item[3],
                    -item[4],
                    item[5],
                    -item[6],
                    item[7],
                    item[8],
                    item[9],
                )
            )
            ranked: list[tuple[tuple[str, str], bool]] = []
            for item in scored:
                has_signal = bool(item[0] > 0 or item[1] > 0 or item[2] > 0 or item[4] > 0)
                ranked.append((item[10], has_signal))
            if len(ranked) == 1 and not ranked[0][1]:
                return [(ranked[0][0], True)]
            return ranked

        for idx, line in enumerate(raw_lines):
            if not isinstance(line, dict):
                continue
            text = line.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            text = text.strip()

            clean_unit_ids: list[str] = []
            seen_unit_ids: set[str] = set()
            raw_unit_ids = line.get("unit_ids")
            if isinstance(raw_unit_ids, list):
                for item in raw_unit_ids:
                    if not isinstance(item, str):
                        continue
                    unit_id = item.strip()
                    if not unit_id or unit_id not in context_unit_ids or unit_id in seen_unit_ids:
                        continue
                    seen_unit_ids.add(unit_id)
                    clean_unit_ids.append(unit_id)

            clean_highlights: list[dict[str, Any]] = []
            seen_hl: set[tuple[str, str]] = set()
            raw_highlights = line.get("highlights")
            if isinstance(raw_highlights, list):
                for item in raw_highlights:
                    if not isinstance(item, dict):
                        continue
                    slide_id = item.get("slide_id")
                    region_id = item.get("region_id")
                    if not isinstance(slide_id, str) or not isinstance(region_id, str):
                        continue
                    key = (slide_id.strip(), region_id.strip())
                    if not key[0] or not key[1] or key in seen_hl:
                        continue
                    region = region_lookup.get(key)
                    if region is None:
                        continue
                    seen_hl.add(key)
                    clean_highlights.append(
                        {
                            "slide_id": key[0],
                            "slide_number": int(region.get("slide_number") or 0),
                            "region_id": key[1],
                        }
                    )

            if not clean_unit_ids and clean_highlights:
                line_tokens = _tokens(text)
                hl_keys = {
                    (str(hl.get("slide_id")), str(hl.get("region_id")))
                    for hl in clean_highlights
                    if isinstance(hl, dict)
                    and isinstance(hl.get("slide_id"), str)
                    and isinstance(hl.get("region_id"), str)
                }
                inferred_scores: list[tuple[int, int, int, str]] = []
                for unit_id, unit in context_unit_lookup.items():
                    if not isinstance(unit, dict):
                        continue
                    unit_slide_id = unit.get("slide_id")
                    raw_unit_region_ids = unit.get("region_ids")
                    if not isinstance(unit_slide_id, str) or not isinstance(raw_unit_region_ids, list):
                        continue
                    overlap_count = 0
                    for rid in raw_unit_region_ids:
                        if not isinstance(rid, str):
                            continue
                        if (unit_slide_id, rid) in hl_keys:
                            overlap_count += 1
                    if overlap_count <= 0:
                        continue
                    unit_text = unit.get("explanation_text")
                    unit_tokens = _tokens(unit_text) if isinstance(unit_text, str) else set()
                    text_overlap = len(line_tokens & unit_tokens) if line_tokens and unit_tokens else 0
                    inferred_scores.append(
                        (
                            overlap_count,
                            text_overlap,
                            int(unit.get("slide_number") or 0),
                            unit_id,
                        )
                    )
                inferred_scores.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
                clean_unit_ids = [item[3] for item in inferred_scores[:2]]

            # Enforce grounded highlights against cited units (no synthetic filling).
            allowed_highlight_keys: set[tuple[str, str]] = set()
            visual_keys: set[tuple[str, str]] = set()
            for unit_id in clean_unit_ids:
                unit = context_unit_lookup.get(unit_id)
                if not isinstance(unit, dict):
                    continue
                unit_slide_id = unit.get("slide_id")
                unit_region_ids = unit.get("region_ids")
                if not isinstance(unit_slide_id, str) or not isinstance(unit_region_ids, list):
                    continue
                for rid in unit_region_ids:
                    if not isinstance(rid, str) or not rid.strip():
                        continue
                    key = (unit_slide_id, rid.strip())
                    if key not in region_lookup:
                        continue
                    allowed_highlight_keys.add(key)
                    region_kind = str((region_lookup.get(key) or {}).get("kind") or "").strip().lower()
                    if region_kind == "visual" or key[1].startswith("v:"):
                        visual_keys.add(key)

            if allowed_highlight_keys:
                clean_highlights = [
                    hl
                    for hl in clean_highlights
                    if isinstance(hl, dict)
                    and isinstance(hl.get("slide_id"), str)
                    and isinstance(hl.get("region_id"), str)
                    and (str(hl.get("slide_id")), str(hl.get("region_id"))) in allowed_highlight_keys
                ]

            if visual_keys:
                candidate_visual_keys: list[tuple[str, str]] = []
                seen_candidates: set[tuple[str, str]] = set()
                for unit_id in clean_unit_ids:
                    unit = context_unit_lookup.get(unit_id)
                    if not isinstance(unit, dict):
                        continue
                    unit_slide_id = unit.get("slide_id")
                    unit_region_ids = unit.get("region_ids")
                    if not isinstance(unit_slide_id, str) or not isinstance(unit_region_ids, list):
                        continue
                    for rid in unit_region_ids:
                        if not isinstance(rid, str) or not rid.strip():
                            continue
                        key = (unit_slide_id, rid.strip())
                        if key not in visual_keys or key in seen_candidates:
                            continue
                        seen_candidates.add(key)
                        candidate_visual_keys.append(key)

                anchor_text_keys: list[tuple[str, str]] = []
                seen_anchor_text: set[tuple[str, str]] = set()
                for hl in clean_highlights:
                    if not isinstance(hl, dict):
                        continue
                    slide_id = hl.get("slide_id")
                    region_id = hl.get("region_id")
                    if not isinstance(slide_id, str) or not isinstance(region_id, str):
                        continue
                    key = (slide_id, region_id)
                    if key not in allowed_highlight_keys:
                        continue
                    if not region_id.startswith("t:"):
                        continue
                    if key in seen_anchor_text:
                        continue
                    seen_anchor_text.add(key)
                    anchor_text_keys.append(key)

                existing_visual: list[dict[str, Any]] = []
                non_visual: list[dict[str, Any]] = []
                for hl in clean_highlights:
                    if not isinstance(hl, dict):
                        continue
                    key = (str(hl.get("slide_id")), str(hl.get("region_id")))
                    if key in visual_keys:
                        existing_visual.append(hl)
                    else:
                        non_visual.append(hl)

                ranked_visual = _rank_visual_keys(
                    line_text=text,
                    candidate_keys=candidate_visual_keys,
                    anchor_text_keys=anchor_text_keys,
                )

                max_visual_per_line = 3
                selected_keys: list[tuple[str, str]] = []
                seen_selected: set[tuple[str, str]] = set()

                for key, has_signal in ranked_visual:
                    if len(selected_keys) >= max_visual_per_line:
                        break
                    if not has_signal:
                        continue
                    if key in seen_selected:
                        continue
                    seen_selected.add(key)
                    selected_keys.append(key)

                for hl in existing_visual:
                    if len(selected_keys) >= max_visual_per_line:
                        break
                    key = (str(hl.get("slide_id")), str(hl.get("region_id")))
                    if key in seen_selected:
                        continue
                    if key not in visual_keys:
                        continue
                    seen_selected.add(key)
                    selected_keys.append(key)

                if not selected_keys and len(candidate_visual_keys) == 1:
                    only_key = candidate_visual_keys[0]
                    selected_keys = [only_key]

                selected_visual: list[dict[str, Any]] = []
                for key in selected_keys:
                    region = region_lookup.get(key)
                    if not isinstance(region, dict):
                        continue
                    selected_visual.append(
                        {
                            "slide_id": key[0],
                            "slide_number": int(region.get("slide_number") or 0),
                            "region_id": key[1],
                        }
                    )

                clean_highlights = selected_visual + non_visual

            out.append(
                {
                    "line_index": idx,
                    "text": text,
                    "highlights": clean_highlights,
                    "unit_ids": clean_unit_ids,
                }
            )

        if not out:
            raise OpenAIJSONError("LLM answer JSON contained no valid answer lines.")

        for idx, line in enumerate(out):
            line["line_index"] = idx
        return out

    @staticmethod
    def _clamp01(value: float) -> float:
        if value < 0.0:
            return 0.0
        if value > 1.0:
            return 1.0
        return float(value)

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    @staticmethod
    def _pack_anchor_units_diverse(
        *,
        anchor_unit_ids: list[str],
        unit_by_id: dict[str, dict[str, Any]],
        max_selected_units: int,
    ) -> list[str]:
        if max_selected_units <= 0:
            return []
        picked: list[str] = []
        seen_ids: set[str] = set()
        seen_slides: set[str] = set()
        normalized_anchors = [uid for uid in anchor_unit_ids if uid in unit_by_id]

        for unit_id in normalized_anchors:
            unit = unit_by_id.get(unit_id) or {}
            slide_id = unit.get("slide_id")
            if not isinstance(slide_id, str):
                slide_id = ""
            if slide_id and slide_id in seen_slides:
                continue
            seen_ids.add(unit_id)
            picked.append(unit_id)
            if slide_id:
                seen_slides.add(slide_id)
            if len(picked) >= max_selected_units:
                return picked

        for unit_id in normalized_anchors:
            if unit_id in seen_ids:
                continue
            seen_ids.add(unit_id)
            picked.append(unit_id)
            if len(picked) >= max_selected_units:
                break
        return picked

    @staticmethod
    def _build_abstention_response(
        *,
        job_id: str,
        question: str,
        message: str,
        selected_unit_ids: list[str],
        expanded_unit_ids: list[str],
        context_slide_ids: list[str],
        timings_ms: dict[str, int],
        reason_codes: list[str],
        debug_payload: dict[str, Any] | None,
        confidence: float = 0.20,
        pipeline_version: str = "v2",
    ) -> dict[str, Any]:
        line = {
            "line_index": 0,
            "text": message.strip(),
            "highlights": [],
            "unit_ids": [],
        }
        response: dict[str, Any] = {
            "job_id": job_id,
            "question": question,
            "answer_text": line["text"],
            "answer_lines": [line],
            "used_context": {
                "selected_unit_ids": list(selected_unit_ids),
                "expanded_unit_ids": list(expanded_unit_ids),
                "context_slide_ids": list(context_slide_ids),
            },
            "timings_ms": timings_ms,
            "answerable": False,
            "confidence": min(0.30, max(0.0, float(confidence))),
            "reason_codes": list(reason_codes),
            "pipeline_version": pipeline_version,
        }
        if debug_payload:
            response["debug"] = debug_payload
        return response

    @staticmethod
    def _build_best_effort_answer_lines(
        *,
        selected_unit_ids: list[str],
        context_units: list[dict[str, Any]],
        region_catalog: list[dict[str, Any]],
        max_lines: int = 2,
    ) -> list[dict[str, Any]]:
        if max_lines <= 0:
            return []

        unit_by_id = {
            str(unit.get("unit_id")): unit
            for unit in context_units
            if isinstance(unit, dict) and isinstance(unit.get("unit_id"), str)
        }
        region_lookup: dict[tuple[str, str], dict[str, Any]] = {}
        for item in region_catalog:
            if not isinstance(item, dict):
                continue
            slide_id = item.get("slide_id")
            region_id = item.get("region_id")
            if isinstance(slide_id, str) and isinstance(region_id, str):
                region_lookup[(slide_id, region_id)] = item

        ordered_unit_ids: list[str] = []
        seen_unit_ids: set[str] = set()
        for unit_id in selected_unit_ids:
            if isinstance(unit_id, str) and unit_id in unit_by_id and unit_id not in seen_unit_ids:
                seen_unit_ids.add(unit_id)
                ordered_unit_ids.append(unit_id)
        for unit in context_units:
            if not isinstance(unit, dict):
                continue
            unit_id = unit.get("unit_id")
            if not isinstance(unit_id, str) or unit_id in seen_unit_ids:
                continue
            seen_unit_ids.add(unit_id)
            ordered_unit_ids.append(unit_id)

        out: list[dict[str, Any]] = []
        seen_text: set[str] = set()
        for unit_id in ordered_unit_ids:
            unit = unit_by_id.get(unit_id) or {}
            raw_text = unit.get("explanation_text")
            if not isinstance(raw_text, str) or not raw_text.strip():
                continue
            text = raw_text.strip()
            text_key = text.lower()
            if text_key in seen_text:
                continue
            seen_text.add(text_key)

            slide_id = unit.get("slide_id")
            raw_region_ids = unit.get("region_ids")
            visual_hls: list[dict[str, Any]] = []
            text_hls: list[dict[str, Any]] = []
            if isinstance(slide_id, str) and isinstance(raw_region_ids, list):
                seen_hl_keys: set[tuple[str, str]] = set()
                for rid in raw_region_ids:
                    if not isinstance(rid, str) or not rid.strip():
                        continue
                    key = (slide_id, rid.strip())
                    if key in seen_hl_keys:
                        continue
                    seen_hl_keys.add(key)
                    region = region_lookup.get(key)
                    if not isinstance(region, dict):
                        continue
                    hl = {
                        "slide_id": key[0],
                        "slide_number": int(region.get("slide_number") or 0),
                        "region_id": key[1],
                    }
                    region_kind = str(region.get("kind") or "").strip().lower()
                    if region_kind == "visual" or key[1].startswith("v:"):
                        visual_hls.append(hl)
                    else:
                        text_hls.append(hl)

            highlights = [*visual_hls[:2], *text_hls[:2]]
            out.append(
                {
                    "line_index": len(out),
                    "text": text,
                    "highlights": highlights,
                    "unit_ids": [unit_id],
                }
            )
            if len(out) >= max_lines:
                break

        return out

    @staticmethod
    def _is_single_function_question(question: str) -> bool:
        q = " ".join(str(question or "").strip().lower().split())
        if not q:
            return False
        patterns = (
            r"\bwhat do\b.+\bdo\b",
            r"\bwhat does\b.+\bdo\b",
            r"\bwhat is the function of\b",
            r"\bwhat is the role of\b",
            r"\bwhat does\b.+\bfunction\b",
        )
        return any(re.search(pattern, q) is not None for pattern in patterns)

    @staticmethod
    def _compact_answer_lines_for_question(
        *,
        question: str,
        answer_lines: list[dict[str, Any]],
        merged_candidates: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], bool]:
        if len(answer_lines) <= 1:
            return answer_lines, False
        if not QuestionAnsweringService._is_single_function_question(question):
            return answer_lines, False

        unit_rrf: dict[str, float] = {}
        for item in merged_candidates:
            if not isinstance(item, dict):
                continue
            unit = item.get("unit")
            if not isinstance(unit, dict):
                continue
            unit_id = unit.get("unit_id")
            if not isinstance(unit_id, str) or not unit_id:
                continue
            try:
                unit_rrf[unit_id] = float(item.get("rrf_score") or 0.0)
            except Exception:
                unit_rrf[unit_id] = 0.0

        action_keywords = (
            "break",
            "digest",
            "recycle",
            "degrade",
            "cleanup",
            "enzyme",
            "waste",
            "dispose",
            "transport",
            "store",
            "synthesize",
            "process",
        )
        detail_requested = "in detail" in str(question or "").lower() or "detailed" in str(question or "").lower()

        best_idx = 0
        best_score = float("-inf")
        for idx, line in enumerate(answer_lines):
            if not isinstance(line, dict):
                continue
            text = str(line.get("text") or "").strip()
            if not text:
                continue
            text_l = text.lower()
            word_count = len(text.split())

            highlights = line.get("highlights")
            has_visual = False
            has_text = False
            if isinstance(highlights, list):
                for hl in highlights:
                    if not isinstance(hl, dict):
                        continue
                    region_id = str(hl.get("region_id") or "")
                    if region_id.startswith("v:"):
                        has_visual = True
                    elif region_id.startswith("t:"):
                        has_text = True

            unit_ids = line.get("unit_ids")
            cited_rrf = 0.0
            if isinstance(unit_ids, list):
                for unit_id in unit_ids:
                    if not isinstance(unit_id, str):
                        continue
                    cited_rrf = max(cited_rrf, float(unit_rrf.get(unit_id, 0.0)))

            action_hits = sum(1 for kw in action_keywords if kw in text_l)
            highlight_score = (1.0 if has_visual else 0.0) + (0.7 if has_text else 0.0)
            length_score = 0.25 if 8 <= word_count <= 28 else 0.0
            detail_score = 0.0
            if detail_requested and word_count >= 12:
                detail_score = min(1.2, 0.15 + (word_count - 12) * 0.04)
            score = (
                2.2 * float(action_hits)
                + 1.3 * float(highlight_score)
                + 6.0 * float(cited_rrf)
                + length_score
                + detail_score
                - 0.02 * abs(word_count - 18)
            )

            if score > best_score:
                best_score = score
                best_idx = idx

        chosen = dict(answer_lines[best_idx]) if isinstance(answer_lines[best_idx], dict) else {}
        chosen["line_index"] = 0
        return [chosen], True

    def _compute_confidence(
        self,
        *,
        answerable: bool,
        answer_lines: list[dict[str, Any]],
        merged_candidates: list[dict[str, Any]],
        verification_summary: dict[str, Any],
        verifier_enabled: bool,
        verifier_failed: bool,
    ) -> float:
        total_lines = len(answer_lines)
        cited_lines = sum(
            1 for line in answer_lines if isinstance(line, dict) and isinstance(line.get("unit_ids"), list) and line.get("unit_ids")
        )
        citation_coverage = (cited_lines / total_lines) if total_lines > 0 else 0.0

        if len(merged_candidates) >= 3:
            score1 = self._safe_float(merged_candidates[0].get("rrf_score"), 0.0)
            score3 = self._safe_float(merged_candidates[2].get("rrf_score"), 0.0)
            retrieval_margin = self._clamp01((score1 - score3) / (abs(score1) + 1e-6))
        else:
            retrieval_margin = 0.5

        if not verifier_enabled or verifier_failed:
            verification_ratio = 0.5
        else:
            verification_ratio = self._clamp01(self._safe_float(verification_summary.get("supported_ratio"), 0.5))

        answerable_component = 1.0 if answerable else 0.0
        score = (
            0.40 * verification_ratio
            + 0.30 * citation_coverage
            + 0.20 * retrieval_margin
            + 0.10 * answerable_component
        )
        return self._clamp01(score)

    @staticmethod
    def _candidate_card_for_rerank(
        *,
        candidate: dict[str, Any],
        index: Any,
        unit_index: int | None,
    ) -> dict[str, Any]:
        unit = candidate.get("unit") if isinstance(candidate.get("unit"), dict) else {}
        card = {
            "unit_id": unit.get("unit_id"),
            "slide_id": unit.get("slide_id"),
            "slide_number": unit.get("slide_number"),
            "step_id": unit.get("step_id"),
            "step_number": unit.get("step_number"),
            "explanation_text": unit.get("explanation_text"),
        }
        if unit_index is not None and unit_index >= 0:
            script_aug = None
            visual_step = None
            region_aux = None
            if hasattr(index, "unit_text_script_aug") and isinstance(index.unit_text_script_aug, list) and unit_index < len(index.unit_text_script_aug):
                script_aug = index.unit_text_script_aug[unit_index]
            if hasattr(index, "unit_text_visual_step") and isinstance(index.unit_text_visual_step, list) and unit_index < len(index.unit_text_visual_step):
                visual_step = index.unit_text_visual_step[unit_index]
            if hasattr(index, "unit_text_region_aux") and isinstance(index.unit_text_region_aux, list) and unit_index < len(index.unit_text_region_aux):
                region_aux = index.unit_text_region_aux[unit_index]
            if isinstance(script_aug, str) and script_aug.strip():
                card["script_aug_text"] = script_aug[:600]
            if isinstance(visual_step, str) and visual_step.strip():
                card["visual_step_text"] = visual_step[:520]
            if isinstance(region_aux, str) and region_aux.strip():
                card["region_aux_text"] = region_aux[:320]
        return card

    @staticmethod
    def _apply_verifier_results(
        *,
        answer_lines: list[dict[str, Any]],
        verdicts: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        if not answer_lines:
            return [], [], {"supported_ratio": 0.0, "partial_count": 0, "unsupported_count": 0}
        verdict_by_idx = {
            int(item["line_index"]): item
            for item in verdicts
            if isinstance(item, dict) and isinstance(item.get("line_index"), int)
        }
        new_lines: list[dict[str, Any]] = []
        details: list[dict[str, Any]] = []
        supported_count = 0
        partial_count = 0
        unsupported_count = 0
        kept_count = 0
        original_count = len(answer_lines)

        for idx, line in enumerate(answer_lines):
            verdict = verdict_by_idx.get(idx)
            if verdict is None:
                raise OpenAIJSONError(f"Verifier response missing verdict for line_index={idx}.")
            verdict_name = str(verdict.get("verdict") or "").strip()
            corrected_text = verdict.get("corrected_text")
            reason_code = str(verdict.get("reason_code") or verdict_name or "unknown").strip()

            if verdict_name == "unsupported":
                unsupported_count += 1
                details.append({"line_index": idx, "verdict": "unsupported", "reason_code": reason_code})
                continue

            line_out = dict(line)
            if verdict_name == "partially_supported":
                partial_count += 1
                if isinstance(corrected_text, str) and corrected_text.strip():
                    line_out["text"] = corrected_text.strip()
                details.append({"line_index": idx, "verdict": "partially_supported", "reason_code": reason_code})
            else:
                supported_count += 1
                details.append({"line_index": idx, "verdict": "supported", "reason_code": reason_code})

            new_lines.append(line_out)
            kept_count += 1

        for idx, line in enumerate(new_lines):
            line["line_index"] = idx

        supported_ratio = (kept_count / original_count) if original_count > 0 else 0.0
        summary = {
            "supported_ratio": supported_ratio,
            "partial_count": partial_count,
            "unsupported_count": unsupported_count,
            "kept_count": kept_count,
            "original_count": original_count,
        }
        return new_lines, details, summary

    def _validate_request_inputs(
        self,
        *,
        job_id: str,
        request: QaAnswerRequest,
    ) -> tuple[str, int, int]:
        job_dir = self.jobs_service.resolve_job_dir(job_id)
        if job_dir is None:
            raise HTTPException(status_code=404, detail="Job not found.")

        question = request.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="Question is required.")

        top_k = int(request.top_k or self.config.qa_default_top_k)
        max_selected_units = int(request.max_selected_units or self.config.qa_default_max_selected_units)
        if top_k < 1 or top_k > 50:
            raise HTTPException(status_code=400, detail="top_k must be between 1 and 50.")
        if max_selected_units < 1 or max_selected_units > 10:
            raise HTTPException(status_code=400, detail="max_selected_units must be between 1 and 10.")
        return question, top_k, max_selected_units

    async def answer_question(
        self,
        *,
        job_id: str,
        request: QaAnswerRequest,
        debug: bool = False,
        progress_cb: Optional[Callable[[dict[str, Any]], Any]] = None,
    ) -> dict[str, Any]:
        question, top_k, max_selected_units = self._validate_request_inputs(job_id=job_id, request=request)

        q_hash = self._stable_question_hash(question)
        logger.info(
            "qa.request_start job_id=%s debug=%s question_len=%d question_hash=%s pipeline=%s",
            job_id,
            bool(debug),
            len(question),
            q_hash,
            "v2",
        )
        await self._emit_progress(
            progress_cb,
            stage="request_start",
            message="Request received.",
            pipeline="v2",
            question_len=len(question),
        )

        return await self._answer_question_v2(
            job_id=job_id,
            question=question,
            top_k=top_k,
            max_selected_units=max_selected_units,
            debug=debug,
            progress_cb=progress_cb,
        )

    async def _answer_question_v2(
        self,
        *,
        job_id: str,
        question: str,
        top_k: int,
        max_selected_units: int,
        debug: bool = False,
        progress_cb: Optional[Callable[[dict[str, Any]], Any]] = None,
    ) -> dict[str, Any]:
        total_started = time.perf_counter()
        timings: dict[str, int] = {
            "index_load_or_build": 0,
            "question_embedding": 0,
            "retrieval": 0,
            "llm_select": 0,  # reused for rerank time for compatibility timings schema
            "llm_answer": 0,
            "total": 0,
        }
        stage_timings: dict[str, int] = {
            "query_rewrite": 0,
            "llm_rerank": 0,
            "context_pack": 0,
            "llm_answerability": 0,
            "llm_verify": 0,
        }
        debug_payload: dict[str, Any] = {"pipeline_version": "v2"} if debug else {}
        reason_codes: list[str] = []

        try:
            async with OpenAIQAService(self.config) as openai_qa:
                index_service = QAIndexService(self.config, openai_qa)

                t0 = time.perf_counter()
                try:
                    index = await index_service.load_or_build_index(job_id, index_version="v2")
                except FileNotFoundError:
                    raise HTTPException(status_code=404, detail="Job not found.")
                except QaIndexUnavailableError as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
                timings["index_load_or_build"] = self._ms(t0)
                await self._emit_progress(
                    progress_cb,
                    stage="index_ready",
                    message="Index loaded.",
                    ms=timings["index_load_or_build"],
                )

                # Query rewrites (optional)
                query_variants: list[str] = [question]
                t_rewrite = time.perf_counter()
                if self.config.qa_enable_query_rewrite and int(self.config.qa_query_rewrite_count or 0) > 0:
                    try:
                        rewrites, raw_rewrites = await openai_qa.rewrite_queries(
                            question=question,
                            max_rewrites=int(self.config.qa_query_rewrite_count),
                        )
                    except OpenAIQATimeoutError as exc:
                        raise HTTPException(status_code=504, detail="QA provider timed out.") from exc
                    except OpenAIQAError as exc:
                        raise HTTPException(status_code=502, detail="QA provider request failed.") from exc
                    except OpenAIJSONError as exc:
                        raise HTTPException(
                            status_code=502,
                            detail="QA provider returned an invalid rewrite response.",
                        ) from exc
                    query_variants = self._unique_in_order([question, *rewrites])[: max(1, 1 + int(self.config.qa_query_rewrite_count))]
                    if debug:
                        debug_payload["query_rewrite_raw"] = raw_rewrites
                stage_timings["query_rewrite"] = self._ms(t_rewrite)
                await self._emit_progress(
                    progress_cb,
                    stage="query_rewrite_done",
                    message="Prepared query variants.",
                    variant_count=len(query_variants),
                    ms=stage_timings["query_rewrite"],
                )
                if debug:
                    debug_payload["query_variants"] = list(query_variants)

                # Embed all variants
                t1 = time.perf_counter()
                try:
                    variant_embeddings = await openai_qa.embed_texts(query_variants)
                except OpenAIQATimeoutError as exc:
                    raise HTTPException(status_code=504, detail="QA provider timed out.") from exc
                except OpenAIQAError as exc:
                    raise HTTPException(status_code=502, detail="QA provider request failed.") from exc
                if len(variant_embeddings) != len(query_variants):
                    raise HTTPException(status_code=502, detail="QA provider returned invalid query embeddings.")
                timings["question_embedding"] = self._ms(t1)
                await self._emit_progress(
                    progress_cb,
                    stage="embedding_ready",
                    message="Question embedding ready.",
                    ms=timings["question_embedding"],
                )

                # Retrieval v2 (explanation-first weighted RRF)
                t2 = time.perf_counter()
                per_query_top_k = max(1, int(self.config.qa_retrieve_top_k_per_query or top_k or 1))
                merged_cap = max(1, int(self.config.qa_merged_candidate_cap or max(top_k, 1)))
                candidates = index_service.rank_candidates_v2(
                    index=index,
                    query_variants=query_variants,
                    query_embeddings=variant_embeddings,
                    top_k_per_query=per_query_top_k,
                    merged_cap=merged_cap,
                )
                timings["retrieval"] = self._ms(t2)
                if not candidates:
                    raise HTTPException(status_code=409, detail="No explanation units available for QA.")
                await self._emit_progress(
                    progress_cb,
                    stage="retrieval_ready",
                    message="Retrieved relevant context.",
                    candidate_count=len(candidates),
                    ms=timings["retrieval"],
                )

                unit_pos_by_id = {
                    str(unit.get("unit_id")): idx
                    for idx, unit in enumerate(index.units)
                    if isinstance(unit, dict) and isinstance(unit.get("unit_id"), str)
                }

                if debug:
                    debug_payload["retrieval_merged_candidates"] = [
                        {
                            **self._candidate_card_from_unit(item["unit"]),
                            "rrf_score": item.get("rrf_score"),
                            "query_contrib_score": item.get("query_contrib_score"),
                            "dense_core": item.get("dense_core"),
                            "bm25_core": item.get("bm25_core"),
                            "dense_script_aug": item.get("dense_script_aug"),
                            "bm25_script_aug": item.get("bm25_script_aug"),
                            "dense_visual_step": item.get("dense_visual_step"),
                            "bm25_visual_step": item.get("bm25_visual_step"),
                            "dense_region_aux": item.get("dense_region_aux"),
                            "bm25_region_aux": item.get("bm25_region_aux"),
                            "has_visual_step": item.get("has_visual_step"),
                            "query_hits": item.get("query_hits"),
                        }
                        for item in candidates
                    ]
                    debug_payload["retrieval_candidates"] = [
                        {
                            **self._candidate_card_from_unit(item["unit"]),
                            "semantic_similarity": item.get("dense_core"),
                            "bm25_score": item.get("bm25_core"),
                            "rrf_score": item.get("rrf_score"),
                        }
                        for item in candidates
                    ]

                # LLM rerank (optional, v2 default on)
                t_rerank = time.perf_counter()
                rerank_pool = candidates[: max(1, int(self.config.qa_rerank_candidates or 20))]
                rerank_cards = [
                    self._candidate_card_for_rerank(
                        candidate=item,
                        index=index,
                        unit_index=unit_pos_by_id.get(str(item["unit"].get("unit_id"))) if isinstance(item.get("unit"), dict) else None,
                    )
                    for item in rerank_pool
                ]
                anchor_unit_ids: list[str] = []
                raw_rerank: Any = None
                if self.config.qa_enable_llm_rerank:
                    try:
                        anchor_unit_ids, raw_rerank = await openai_qa.rerank_candidate_units(
                            question=question,
                            candidate_cards=rerank_cards,
                            top_n=max(1, int(self.config.qa_rerank_top_n or 8)),
                        )
                    except OpenAIQATimeoutError as exc:
                        raise HTTPException(status_code=504, detail="QA provider timed out.") from exc
                    except OpenAIQAError as exc:
                        raise HTTPException(status_code=502, detail="QA provider request failed.") from exc
                    except OpenAIJSONError as exc:
                        raise HTTPException(
                            status_code=502,
                            detail="QA provider returned an invalid rerank response.",
                        ) from exc
                else:
                    anchor_unit_ids = [
                        str(item["unit"]["unit_id"])
                        for item in rerank_pool
                        if isinstance(item.get("unit"), dict) and isinstance(item["unit"].get("unit_id"), str)
                    ][: max(1, int(self.config.qa_rerank_top_n or 8))]

                if not anchor_unit_ids:
                    raise HTTPException(status_code=502, detail="QA provider returned no reranked context units.")
                stage_timings["llm_rerank"] = self._ms(t_rerank)
                timings["llm_select"] = stage_timings["llm_rerank"]
                await self._emit_progress(
                    progress_cb,
                    stage="rerank_done",
                    message="Reranked context units.",
                    selected_count=len(anchor_unit_ids),
                    ms=stage_timings["llm_rerank"],
                )
                if debug:
                    debug_payload["rerank_input_unit_ids"] = [
                        str(item["unit"]["unit_id"])
                        for item in rerank_pool
                        if isinstance(item.get("unit"), dict) and isinstance(item["unit"].get("unit_id"), str)
                    ]
                    debug_payload["rerank_output_unit_ids"] = list(anchor_unit_ids)
                    debug_payload["rerank_raw"] = raw_rerank

                # Context packing (diversity + neighbor expansion)
                t_pack = time.perf_counter()
                selected_unit_ids = self._pack_anchor_units_diverse(
                    anchor_unit_ids=anchor_unit_ids,
                    unit_by_id=index.unit_by_id,
                    max_selected_units=max_selected_units,
                )
                if not selected_unit_ids:
                    raise HTTPException(status_code=409, detail="No context units available after context packing.")
                expanded_unit_ids = self._expand_context_units(
                    selected_unit_ids=selected_unit_ids,
                    all_units=index.units,
                    max_expanded_units=12,
                )
                if not expanded_unit_ids:
                    raise HTTPException(status_code=409, detail="No context units available after selection.")
                context_units = [index.unit_by_id[unit_id] for unit_id in expanded_unit_ids if unit_id in index.unit_by_id]
                context_unit_cards = [self._candidate_card_from_unit(unit) for unit in context_units]

                region_catalog, region_catalog_summary = self._build_region_catalog_for_context(
                    region_catalog_all=index.region_catalog,
                    context_units=context_units,
                    only_referenced_regions=True,
                    extra_visual_regions_per_slide=8,
                    extra_text_regions_per_slide=2,
                    max_total_regions=96,
                )
                context_slide_ids = region_catalog_summary.get("context_slide_ids")
                if not isinstance(context_slide_ids, list):
                    context_slide_ids = self._unique_in_order(
                        [str(unit.get("slide_id")) for unit in context_units if isinstance(unit.get("slide_id"), str)]
                    )
                stage_timings["context_pack"] = self._ms(t_pack)
                await self._emit_progress(
                    progress_cb,
                    stage="context_ready",
                    message="Context packed for answer generation.",
                    context_unit_count=len(context_units),
                    region_count=len(region_catalog),
                    ms=stage_timings["context_pack"],
                )

                if debug:
                    debug_payload["pass2_prompt_unit_ids"] = list(expanded_unit_ids)
                    debug_payload["region_catalog_summary"] = region_catalog_summary

                # Answerability gate
                answerable = True
                t_gate = time.perf_counter()
                raw_answerability: Any = None
                if self.config.qa_enable_answerability_gate:
                    try:
                        gate_result, raw_answerability = await openai_qa.assess_answerability(
                            question=question,
                            context_units=context_unit_cards,
                        )
                        answerable = bool(gate_result.get("answerable"))
                        reason_code = gate_result.get("reason_code")
                        if isinstance(reason_code, str) and reason_code.strip():
                            if reason_code not in reason_codes:
                                reason_codes.append(reason_code)
                    except OpenAIQATimeoutError as exc:
                        raise HTTPException(status_code=504, detail="QA provider timed out.") from exc
                    except (OpenAIQAError, OpenAIJSONError) as exc:
                        raise HTTPException(
                            status_code=502,
                            detail="QA provider returned an invalid answerability response.",
                        ) from exc
                stage_timings["llm_answerability"] = self._ms(t_gate)
                await self._emit_progress(
                    progress_cb,
                    stage="answerability_done",
                    message="Answerability checked.",
                    answerable=bool(answerable),
                    ms=stage_timings["llm_answerability"],
                )
                if debug:
                    debug_payload["answerability_raw"] = raw_answerability

                if not answerable:
                    reason_codes.append("low_evidence_best_effort")

                # Answer generation
                t4 = time.perf_counter()
                await self._emit_progress(
                    progress_cb,
                    stage="answer_generating",
                    message="Generating answer.",
                )
                try:
                    raw_answer = await openai_qa.answer_with_highlights(
                        question=question,
                        context_units=context_unit_cards,
                        region_catalog=region_catalog,
                    )
                    answer_lines = self._validate_answer_lines(
                        raw_answer=raw_answer,
                        context_units=context_units,
                        region_catalog=region_catalog,
                    )
                except OpenAIQATimeoutError as exc:
                    raise HTTPException(status_code=504, detail="QA provider timed out.") from exc
                except OpenAIJSONError as exc:
                    raise HTTPException(
                        status_code=502,
                        detail="QA provider returned an invalid structured response.",
                    ) from exc
                except OpenAIQAError as exc:
                    raise HTTPException(status_code=502, detail="QA provider request failed.") from exc
                timings["llm_answer"] = self._ms(t4)
                await self._emit_progress(
                    progress_cb,
                    stage="answer_ready",
                    message="Answer generated.",
                    line_count=len(answer_lines),
                    ms=timings["llm_answer"],
                )

                # Verification (optional)
                verifier_failed = False
                used_fallback_answer = False
                verification_details: list[dict[str, Any]] = []
                verification_summary = {"supported_ratio": 0.5, "partial_count": 0, "unsupported_count": 0}
                t_verify = time.perf_counter()
                raw_verify: Any = None
                if self.config.qa_enable_verifier and answer_lines:
                    try:
                        verdicts, raw_verify = await openai_qa.verify_answer_lines(
                            question=question,
                            answer_lines=answer_lines,
                            context_units=context_unit_cards,
                        )
                        if not verdicts:
                            raise HTTPException(status_code=502, detail="QA provider returned no verifier verdicts.")
                        answer_lines, verification_details, verification_summary = self._apply_verifier_results(
                            answer_lines=answer_lines,
                            verdicts=verdicts,
                        )
                        if verification_summary.get("partial_count", 0):
                            reason_codes.append("partial_verification")
                        if not answer_lines:
                            reason_codes.append("verification_removed_all_lines")
                    except OpenAIQATimeoutError as exc:
                        raise HTTPException(status_code=504, detail="QA provider timed out.") from exc
                    except (OpenAIQAError, OpenAIJSONError) as exc:
                        raise HTTPException(
                            status_code=502,
                            detail="QA provider returned an invalid verification response.",
                        ) from exc
                stage_timings["llm_verify"] = self._ms(t_verify)
                await self._emit_progress(
                    progress_cb,
                    stage="verification_done",
                    message="Verification complete.",
                    kept_line_count=len(answer_lines),
                    ms=stage_timings["llm_verify"],
                )
                if debug:
                    debug_payload["verification_raw"] = raw_verify
                    debug_payload["verification_results"] = verification_details

                if not answer_lines:
                    fallback_lines = self._build_best_effort_answer_lines(
                        selected_unit_ids=selected_unit_ids,
                        context_units=context_units,
                        region_catalog=region_catalog,
                        max_lines=2,
                    )
                    if fallback_lines:
                        used_fallback_answer = True
                        answer_lines = fallback_lines
                        reason_codes.append("fallback_context_answer")

                if not answer_lines:
                    raise HTTPException(
                        status_code=409,
                        detail="No reliable explanation units were available to construct an answer.",
                    )

                answer_lines, compacted_single = self._compact_answer_lines_for_question(
                    question=question,
                    answer_lines=answer_lines,
                    merged_candidates=candidates,
                )
                if compacted_single:
                    reason_codes.append("focused_single_line")

                confidence = self._compute_confidence(
                    answerable=bool(answerable),
                    answer_lines=answer_lines,
                    merged_candidates=candidates,
                    verification_summary=verification_summary,
                    verifier_enabled=bool(self.config.qa_enable_verifier),
                    verifier_failed=verifier_failed,
                )
                if used_fallback_answer:
                    confidence = min(confidence, 0.35)
                if confidence < 0.40 and "low_confidence" not in reason_codes:
                    reason_codes.append("low_confidence")
                response = {
                    "job_id": job_id,
                    "question": question,
                    "answer_text": "\n".join(line["text"] for line in answer_lines),
                    "answer_lines": answer_lines,
                    "used_context": {
                        "selected_unit_ids": selected_unit_ids,
                        "expanded_unit_ids": expanded_unit_ids,
                        "context_slide_ids": context_slide_ids,
                    },
                    "timings_ms": {},
                    "answerable": bool(answerable),
                    "confidence": confidence,
                    "reason_codes": self._unique_in_order(reason_codes),
                    "pipeline_version": "v2",
                }
                if debug:
                    response["debug"] = debug_payload

                if debug:
                    debug_payload["stage_timings_ms"] = dict(stage_timings)

        finally:
            timings["total"] = self._ms(total_started)
            if "response" in locals() and isinstance(response, dict):
                response["timings_ms"] = {
                    "index_load_or_build": timings["index_load_or_build"],
                    "question_embedding": timings["question_embedding"],
                    "retrieval": timings["retrieval"],
                    "llm_select": timings["llm_select"],
                    "llm_answer": timings["llm_answer"],
                    "total": timings["total"],
                }
            logger.info("qa.request_done job_id=%s pipeline=v2 total_ms=%d", job_id, timings["total"])
            await self._emit_progress(
                progress_cb,
                stage="request_done",
                message="Request completed.",
                total_ms=timings["total"],
                pipeline="v2",
            )

        response["timings_ms"] = {
            "index_load_or_build": timings["index_load_or_build"],
            "question_embedding": timings["question_embedding"],
            "retrieval": timings["retrieval"],
            "llm_select": timings["llm_select"],
            "llm_answer": timings["llm_answer"],
            "total": timings["total"],
        }
        # Enforce additive fields even if abstention helper already filled them.
        response.setdefault("answerable", True)
        response.setdefault("confidence", None)
        response.setdefault("reason_codes", [])
        response["reason_codes"] = self._unique_in_order(
            [str(v) for v in response.get("reason_codes", []) if isinstance(v, str)]
        )
        response["pipeline_version"] = "v2"
        if not response.get("answerable"):
            response["confidence"] = min(0.30, self._safe_float(response.get("confidence"), 0.20))
        if debug and isinstance(response.get("debug"), dict):
            response["debug"].setdefault("pipeline_version", "v2")
        return response
