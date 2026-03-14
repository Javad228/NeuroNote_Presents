from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import cv2

from ..config import AppConfig
from .jobs import JobsService
from .text_recolor import recolor_text_simple


_SLIDE_NAME_RE = re.compile(r"^page_\d{3}\.png$")
_SLIDE_INDEX_RE = re.compile(r"page_(\d+)$")
_IMAGE_NAME_RE = re.compile(r"^page_\d+$")
_RENDERED_FILE_RE = re.compile(r"^step_\d{3}\.jpg$")
_AUDIO_SUFFIXES = (".wav", ".mp3", ".m4a", ".ogg")
_TRANSCRIPT_TIMESTAMPS_FILE = "transcript_audio_timestamps.json"


class LectureService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.jobs_service = JobsService(config)
        candidates = [
            config.neuronote_pipeline_root / "neuronote" / "jobs",
            config.neuronote_pipeline_root / "jobs",
            config.neuronote_pipeline_root,
            *config.neuronote_artifact_roots,
            # Local fallback when artifacts are written by the separate SlideParser backend.
            Path.home() / "NeuroPresentsBackend" / "neuropresentsbackend" / "jobs",
        ]

        self._output_roots: list[Path] = []
        seen: set[str] = set()
        for root in candidates:
            try:
                resolved = root.resolve()
            except Exception:
                continue
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            self._output_roots.append(resolved)

    @staticmethod
    def _read_json(path: Path | None) -> dict[str, Any] | None:
        if path is None:
            return None
        try:
            payload = json.loads(path.read_text())
            if isinstance(payload, dict):
                return payload
        except Exception:
            return None
        return None

    @staticmethod
    def _parse_slide_number(image_name: str) -> int | None:
        match = _SLIDE_INDEX_RE.match(image_name)
        if not match:
            return None
        try:
            return int(match.group(1))
        except Exception:
            return None

    def _resolve_neuronote_artifact(self, artifact_url: str) -> Path | None:
        if not artifact_url or not isinstance(artifact_url, str):
            return None
        if not artifact_url.startswith("/"):
            return None

        rel = artifact_url.lstrip("/")
        if ".." in rel.split("/"):
            return None

        for root in self._output_roots:
            candidate = (root / rel).resolve()
            try:
                candidate.relative_to(root)
            except Exception:
                continue
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _extract_visual_plan_by_step(debug_payload: dict[str, Any] | None) -> dict[int, dict[str, str]]:
        if not isinstance(debug_payload, dict):
            return {}
        raw_steps: Any = None

        response_payload = debug_payload.get("visual_traversal_candidate_plan_response")
        if isinstance(response_payload, dict) and isinstance(response_payload.get("steps"), list):
            raw_steps = response_payload.get("steps")

        if raw_steps is None:
            raw_response = debug_payload.get("visual_traversal_candidate_plan_raw_response")
            if isinstance(raw_response, str) and raw_response.strip():
                try:
                    parsed = json.loads(raw_response)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict) and isinstance(parsed.get("steps"), list):
                    raw_steps = parsed.get("steps")

        if not isinstance(raw_steps, list):
            return {}

        out: dict[int, dict[str, str]] = {}
        for item in raw_steps:
            if not isinstance(item, dict):
                continue
            try:
                step_number = int(item.get("step_number"))
            except Exception:
                continue
            if step_number <= 0:
                continue
            what = item.get("what")
            description = item.get("description_of_how_it_looks")
            entry: dict[str, str] = {}
            if isinstance(what, str) and what.strip():
                entry["what"] = what.strip()
            if isinstance(description, str) and description.strip():
                entry["description_of_how_it_looks"] = description.strip()
            if entry:
                out[step_number] = entry
        return out

    def _resolve_sibling_artifact(self, artifact_url: Any, sibling_filename: str) -> Path | None:
        if not isinstance(artifact_url, str) or not artifact_url.strip():
            return None
        try:
            sibling_url = str(Path(artifact_url).with_name(sibling_filename))
        except Exception:
            return None
        return self._resolve_neuronote_artifact(sibling_url)

    @staticmethod
    def _normalize_bbox(raw: Any) -> list[float] | None:
        if not isinstance(raw, list) or len(raw) != 4:
            return None
        try:
            x1, y1, x2, y2 = [float(v) for v in raw]
        except Exception:
            return None
        return [x1, y1, x2, y2]

    @staticmethod
    def _normalize_polygon(raw: Any) -> list[list[float]] | None:
        if not isinstance(raw, list):
            return None
        points: list[list[float]] = []
        for pt in raw:
            if not isinstance(pt, list) or len(pt) < 2:
                continue
            try:
                x = float(pt[0])
                y = float(pt[1])
            except Exception:
                continue
            points.append([x, y])
        return points if len(points) >= 3 else None

    def _extract_region_payload(self, regions_payload: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(regions_payload, dict):
            return {
                "image_width": None,
                "image_height": None,
                "regions": [],
                "clusters": [],
                "groups": [],
            }

        metadata = regions_payload.get("metadata")
        image_width: float | None = None
        image_height: float | None = None
        if isinstance(metadata, dict):
            try:
                image_width = float(metadata.get("image_width"))
            except Exception:
                image_width = None
            try:
                image_height = float(metadata.get("image_height"))
            except Exception:
                image_height = None

        out_regions: list[dict[str, Any]] = []
        raw_regions = regions_payload.get("regions")
        if isinstance(raw_regions, list):
            for region in raw_regions:
                if not isinstance(region, dict):
                    continue
                region_id = region.get("id")
                kind = region.get("kind")
                bbox = self._normalize_bbox(region.get("bbox"))
                if not isinstance(region_id, str) or not region_id:
                    continue
                if not isinstance(kind, str) or not kind:
                    continue
                if bbox is None:
                    continue

                polygons: list[list[list[float]]] = []
                raw_polygons = region.get("polygons")
                if isinstance(raw_polygons, list):
                    for poly in raw_polygons:
                        normalized = self._normalize_polygon(poly)
                        if normalized is not None:
                            polygons.append(normalized)

                out_regions.append(
                    {
                        "id": region_id,
                        "kind": kind,
                        "bbox": bbox,
                        "polygon": self._normalize_polygon(region.get("polygon")),
                        "polygons": polygons,
                        "sam_expanded": bool(region.get("sam_expanded", True)),
                        "display": region.get("display") if isinstance(region.get("display"), str) else None,
                    }
                )

        out_clusters: list[dict[str, Any]] = []
        raw_clusters = regions_payload.get("clusters")
        if isinstance(raw_clusters, list):
            for cluster in raw_clusters:
                if not isinstance(cluster, dict):
                    continue
                cluster_id = cluster.get("id")
                bbox = self._normalize_bbox(cluster.get("bbox"))
                region_ids = cluster.get("region_ids")
                if not isinstance(cluster_id, str) or not cluster_id:
                    continue
                if bbox is None:
                    continue
                if not isinstance(region_ids, list):
                    region_ids = []

                out_clusters.append(
                    {
                        "id": cluster_id,
                        "bbox": bbox,
                        "region_ids": [rid for rid in region_ids if isinstance(rid, str)],
                    }
                )

        out_groups: list[dict[str, Any]] = []
        raw_groups = regions_payload.get("groups")
        if isinstance(raw_groups, list):
            for group in raw_groups:
                if not isinstance(group, dict):
                    continue
                group_id = group.get("id")
                bbox = self._normalize_bbox(group.get("bbox"))
                children = group.get("children")
                if not isinstance(group_id, str) or not group_id:
                    continue
                if bbox is None:
                    continue
                if not isinstance(children, list):
                    children = []

                out_groups.append(
                    {
                        "id": group_id,
                        "bbox": bbox,
                        "children": [child for child in children if isinstance(child, str)],
                    }
                )

        return {
            "image_width": image_width,
            "image_height": image_height,
            "regions": out_regions,
            "clusters": out_clusters,
            "groups": out_groups,
        }

    @staticmethod
    def _extract_steps(
        script_payload: dict[str, Any] | None,
        *,
        debug_payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(script_payload, dict):
            return []
        raw_steps = script_payload.get("steps")
        if not isinstance(raw_steps, list):
            return []

        visual_plan_by_step = LectureService._extract_visual_plan_by_step(debug_payload)
        steps: list[dict[str, Any]] = []
        elapsed = 0
        for idx, step in enumerate(raw_steps, start=1):
            if not isinstance(step, dict):
                continue

            line = step.get("line")
            if not isinstance(line, str) or not line.strip():
                continue

            dwell_ms = step.get("dwell_ms")
            if not isinstance(dwell_ms, int) or dwell_ms <= 0:
                dwell_ms = 3500

            try:
                step_number = int(step.get("step_number"))
            except Exception:
                step_number = idx
            if step_number <= 0:
                step_number = idx

            region_ids = step.get("region_ids")
            if not isinstance(region_ids, list):
                region_ids = []

            what = step.get("what")
            description = step.get("description_of_how_it_looks")
            if not (isinstance(what, str) and what.strip()):
                what = (visual_plan_by_step.get(step_number) or {}).get("what")
            if not (isinstance(description, str) and description.strip()):
                description = (visual_plan_by_step.get(step_number) or {}).get("description_of_how_it_looks")

            step_row: dict[str, Any] = {
                "step_id": str(step.get("step_id") or f"s{idx}"),
                "line": line.strip(),
                "region_ids": [str(v) for v in region_ids if isinstance(v, str)],
                "dwell_ms": dwell_ms,
                "start_ms": elapsed,
                "step_number": step_number,
            }
            if isinstance(what, str) and what.strip():
                step_row["what"] = what.strip()
            if isinstance(description, str) and description.strip():
                step_row["description_of_how_it_looks"] = description.strip()

            steps.append(step_row)
            elapsed += dwell_ms

        return steps

    def get_input_pdf_path(self, job_id: str) -> Path | None:
        job_dir = self.jobs_service.resolve_job_dir(job_id)
        if job_dir is None:
            return None
        pdf = job_dir / "input.pdf"
        if not pdf.exists() or not pdf.is_file():
            return None
        return pdf

    def get_transcript_audio_path(self, job_id: str) -> Path | None:
        job_dir = self.jobs_service.resolve_job_dir(job_id)
        if job_dir is None:
            return None

        preferred = [
            job_dir / "transcript_audio.wav",
            job_dir / "transcript_audio.mp3",
        ]
        for path in preferred:
            if path.exists() and path.is_file():
                return path

        for suffix in _AUDIO_SUFFIXES:
            for path in sorted(job_dir.glob(f"*{suffix}")):
                if path.exists() and path.is_file():
                    return path

        return None

    @staticmethod
    def _build_audio_timing_map(job_dir: Path) -> dict[tuple[int, int], tuple[int, int]]:
        timing_path = job_dir / _TRANSCRIPT_TIMESTAMPS_FILE
        if not timing_path.exists() or not timing_path.is_file():
            return {}

        try:
            payload = json.loads(timing_path.read_text())
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}

        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list):
            return {}

        timing_map: dict[tuple[int, int], tuple[int, int]] = {}
        for item in raw_steps:
            if not isinstance(item, dict):
                continue
            try:
                slide_number = int(item.get("slide_number"))
                step_number = int(item.get("step_number"))
                start_ms = int(item.get("audio_start_ms"))
                end_ms = int(item.get("audio_end_ms"))
            except Exception:
                continue
            if slide_number <= 0 or step_number <= 0:
                continue
            if end_ms <= start_ms:
                continue
            timing_map[(slide_number, step_number)] = (start_ms, end_ms)

        return timing_map

    def get_slide_image_path(self, job_id: str, slide_name: str) -> Path | None:
        if not _SLIDE_NAME_RE.match(slide_name):
            return None

        job_dir = self.jobs_service.resolve_job_dir(job_id)
        if job_dir is None:
            return None

        slide_path = (job_dir / "slides" / "images" / slide_name).resolve()
        base = (job_dir / "slides" / "images").resolve()
        try:
            slide_path.relative_to(base)
        except Exception:
            return None
        if not slide_path.exists() or not slide_path.is_file():
            return None
        return slide_path

    def get_precomputed_rendered_image_path(
        self,
        job_id: str,
        image_name: str,
        filename: str,
    ) -> Path | None:
        if not _IMAGE_NAME_RE.match(image_name):
            return None
        if not _RENDERED_FILE_RE.match(filename):
            return None

        job_dir = self.jobs_service.resolve_job_dir(job_id)
        if job_dir is None:
            return None

        base = (job_dir / "rendered_steps").resolve()
        candidate = (base / image_name / filename).resolve()
        try:
            candidate.relative_to(base)
        except Exception:
            return None
        if not candidate.exists() or not candidate.is_file():
            return None
        return candidate

    def get_lecture_payload(
        self,
        job_id: str,
        *,
        ensure_rendered_steps: bool = True,
    ) -> dict[str, Any] | None:
        job_dir = self.jobs_service.resolve_job_dir(job_id)
        if job_dir is None:
            return None

        result_path = job_dir / "result.json"
        if not result_path.exists() or not result_path.is_file():
            return None

        result_payload = self._read_json(result_path)
        if result_payload is None:
            return None

        result_title = result_payload.get("title")
        title = result_title.strip() if isinstance(result_title, str) and result_title.strip() else ""
        input_pdf_name = "input.pdf"
        input_pdf_path = self.get_input_pdf_path(job_id)
        if input_pdf_path is not None:
            input_pdf_name = input_pdf_path.name
            if not title:
                title = input_pdf_path.stem
                if input_pdf_name.lower() == "input.pdf":
                    title = f"Lecture {job_id}"
        elif not title:
            title = job_id

        slides: list[dict[str, Any]] = []
        neuronote_chunks = result_payload.get("neuronote_chunks")
        if isinstance(neuronote_chunks, list):
            for chunk in neuronote_chunks:
                if not isinstance(chunk, dict):
                    continue
                chunk_id = chunk.get("chunk_id")
                chunk_id_str = str(chunk_id).strip() if isinstance(chunk_id, str) else None
                if chunk_id_str == "":
                    chunk_id_str = None
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
                    object_path = image.get("object_path")
                    slide_file: str | None = None
                    if isinstance(object_path, str) and object_path.strip():
                        slide_file = Path(object_path).name

                    image_name = image.get("image_name")
                    if slide_file is None and isinstance(image_name, str) and image_name:
                        slide_file = f"{image_name}.png"
                    if slide_file is None:
                        continue

                    local_image_name = Path(slide_file).stem
                    slide_number = self._parse_slide_number(local_image_name)
                    if slide_number is None and isinstance(image_name, str):
                        slide_number = self._parse_slide_number(image_name)
                    if slide_number is None:
                        continue

                    if self.get_slide_image_path(job_id, slide_file) is None:
                        continue

                    script_payload = self._read_json(
                        self._resolve_neuronote_artifact(image.get("script_url"))
                    )
                    debug_prompts_payload = self._read_json(
                        self._resolve_sibling_artifact(image.get("script_url"), "debug_prompts.json")
                    )
                    regions_payload = self._read_json(
                        self._resolve_neuronote_artifact(image.get("regions_url"))
                    )
                    steps = self._extract_steps(script_payload, debug_payload=debug_prompts_payload)
                    region_payload = self._extract_region_payload(regions_payload)
                    rendered_step_urls: list[str] = []
                    if ensure_rendered_steps:
                        rendered_step_urls = self.ensure_rendered_step_images(
                            job_id=job_id,
                            image_name=local_image_name,
                            steps=steps,
                            regions=region_payload["regions"],
                            clusters=region_payload["clusters"],
                            groups=region_payload["groups"],
                        )

                    slides.append(
                        {
                            "slide_number": slide_number,
                            "slide_id": local_image_name,
                            "chunk_id": chunk_id_str,
                            "image_name": local_image_name,
                            "image_url": f"/api/jobs/{job_id}/slides/{slide_file}",
                            "script_title": script_payload.get("title") if isinstance(script_payload, dict) else None,
                            "script_summary": script_payload.get("summary") if isinstance(script_payload, dict) else None,
                            "steps": steps,
                            "regions": region_payload["regions"],
                            "clusters": region_payload["clusters"],
                            "groups": region_payload["groups"],
                            "image_width": region_payload["image_width"],
                            "image_height": region_payload["image_height"],
                            "rendered_step_urls": rendered_step_urls,
                        }
                    )

        if not slides:
            images_dir = job_dir / "slides" / "images"
            if images_dir.exists() and images_dir.is_dir():
                for slide_path in sorted(images_dir.glob("page_*.png")):
                    match = re.match(r"^page_(\d+)\.png$", slide_path.name)
                    if not match:
                        continue
                    slides.append(
                        {
                            "slide_number": int(match.group(1)),
                            "slide_id": slide_path.stem,
                            "chunk_id": None,
                            "image_name": slide_path.stem,
                            "image_url": f"/api/jobs/{job_id}/slides/{slide_path.name}",
                            "script_title": None,
                            "script_summary": None,
                            "steps": [],
                            "regions": [],
                            "clusters": [],
                            "groups": [],
                            "image_width": None,
                            "image_height": None,
                            "rendered_step_urls": [],
                        }
                    )

        # Keep one entry per slide index.
        dedup: dict[int, dict[str, Any]] = {}
        for slide in slides:
            idx = slide["slide_number"]
            if idx not in dedup:
                dedup[idx] = slide
        ordered = [dedup[k] for k in sorted(dedup.keys())]
        audio_timing_map = self._build_audio_timing_map(job_dir)
        if audio_timing_map:
            for slide in ordered:
                slide_number = slide.get("slide_number")
                if not isinstance(slide_number, int):
                    continue
                slide_steps = slide.get("steps")
                if not isinstance(slide_steps, list):
                    continue
                for step in slide_steps:
                    if not isinstance(step, dict):
                        continue
                    step_number = step.get("step_number")
                    if not isinstance(step_number, int):
                        continue
                    timing = audio_timing_map.get((slide_number, step_number))
                    if timing is None:
                        continue
                    step["audio_start_ms"] = timing[0]
                    step["audio_end_ms"] = timing[1]

        return {
            "job_id": job_id,
            "title": title,
            "input_pdf_name": input_pdf_name,
            "input_pdf_url": f"/api/jobs/{job_id}/input-pdf",
            "audio_url": (
                f"/api/jobs/{job_id}/audio"
                if self.get_transcript_audio_path(job_id) is not None
                else None
            ),
            "page_count": result_payload.get("page_count"),
            "total_slides": len(ordered),
            "slides": ordered,
        }

    @staticmethod
    def _encode_jpeg(image_bgr: Any) -> bytes | None:
        try:
            ok, encoded = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            if not ok:
                return None
            return encoded.tobytes()
        except Exception:
            return None

    @staticmethod
    def _resolve_active_ids(
        region_ids: list[str],
        clusters: list[dict[str, Any]],
        groups: list[dict[str, Any]],
    ) -> set[str]:
        cluster_map = {
            c["id"]: c for c in clusters if isinstance(c, dict) and isinstance(c.get("id"), str)
        }
        group_map = {g["id"]: g for g in groups if isinstance(g, dict) and isinstance(g.get("id"), str)}

        visited: set[str] = set()

        def activate(eid: str) -> None:
            if not eid or eid in visited:
                return
            visited.add(eid)

            if eid.startswith("g:"):
                group = group_map.get(eid)
                children = group.get("children") if isinstance(group, dict) else None
                if isinstance(children, list):
                    for child in children:
                        if isinstance(child, str):
                            activate(child)
                return

            if eid.startswith("c:"):
                cluster = cluster_map.get(eid)
                members = cluster.get("region_ids") if isinstance(cluster, dict) else None
                if isinstance(members, list):
                    for rid in members:
                        if isinstance(rid, str):
                            activate(rid)

        for region_id in region_ids:
            if isinstance(region_id, str):
                activate(region_id)

        return visited

    def render_step_text_recolor_image(
        self,
        *,
        job_id: str,
        slide_name: str,
        step_index: int,
        strength: float = 1.0,
    ) -> bytes | None:
        slide_path = self.get_slide_image_path(job_id, slide_name)
        if slide_path is None:
            return None

        lecture = self.get_lecture_payload(job_id)
        if lecture is None:
            return None

        image_name = slide_name.rsplit(".", 1)[0]
        slide = None
        for item in lecture.get("slides", []):
            if isinstance(item, dict) and item.get("image_name") == image_name:
                slide = item
                break
        if slide is None:
            return None

        image = cv2.imread(str(slide_path))
        if image is None:
            return None

        steps = slide.get("steps")
        if not isinstance(steps, list) or step_index < 0 or step_index >= len(steps):
            return self._encode_jpeg(image)

        step = steps[step_index]
        if not isinstance(step, dict):
            return self._encode_jpeg(image)

        active_ids = self._resolve_active_ids(
            region_ids=step.get("region_ids") if isinstance(step.get("region_ids"), list) else [],
            clusters=slide.get("clusters") if isinstance(slide.get("clusters"), list) else [],
            groups=slide.get("groups") if isinstance(slide.get("groups"), list) else [],
        )

        regions = slide.get("regions")
        if not isinstance(regions, list):
            return self._encode_jpeg(image)

        region_map = {
            region.get("id"): region
            for region in regions
            if isinstance(region, dict) and isinstance(region.get("id"), str)
        }

        h_img, w_img = image.shape[:2]
        pad = 2
        strength = max(0.0, min(2.0, float(strength)))

        for region_id in active_ids:
            region = region_map.get(region_id)
            if not isinstance(region, dict):
                continue
            if region.get("kind") != "text":
                continue

            bbox = region.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue

            try:
                x1, y1, x2, y2 = [int(round(float(v))) for v in bbox]
            except Exception:
                continue

            x1, x2 = min(x1, x2), max(x1, x2)
            y1, y2 = min(y1, y2), max(y1, y2)
            x1_p, y1_p = max(0, x1 - pad), max(0, y1 - pad)
            x2_p, y2_p = min(w_img, x2 + pad), min(h_img, y2 + pad)

            if x2_p - x1_p < 3 or y2_p - y1_p < 3:
                continue

            crop = image[y1_p:y2_p, x1_p:x2_p]
            image[y1_p:y2_p, x1_p:x2_p] = recolor_text_simple(crop, strength=strength)

        return self._encode_jpeg(image)

    def ensure_rendered_step_images(
        self,
        *,
        job_id: str,
        image_name: str,
        steps: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        clusters: list[dict[str, Any]],
        groups: list[dict[str, Any]],
        strength: float = 1.0,
    ) -> list[str]:
        if not _IMAGE_NAME_RE.match(image_name):
            return []
        if not isinstance(steps, list) or not steps:
            return []
        slide_name = f"{image_name}.png"
        if self.get_slide_image_path(job_id, slide_name) is None:
            return []

        strength = max(0.0, min(2.0, float(strength)))
        urls: list[str] = []
        for idx in range(len(steps)):
            url = f"/api/jobs/{job_id}/slides/{slide_name}/rendered?step_index={idx}"
            if abs(strength - 1.0) > 1e-6:
                url = f"{url}&strength={strength:.3f}".rstrip("0").rstrip(".")
            urls.append(url)
        return urls
