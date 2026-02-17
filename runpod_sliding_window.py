#!/usr/bin/env python3
"""
Run sliding-window crops over an image and send batched RunPod requests.

Example:
python3 runpod_sliding_window.py \
  --image test.png \
  --text "a box. an arrow. a person." \
  --endpoint-id hwq68dshsvq17 \
  --window-fraction 0.35 \
  --min-window-width 512 \
  --min-window-height 384 \
  --stride-x 768 \
  --stride-y 768 \
  --batch-size 8 \
  --overlay-output test_overlay.png
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None  # type: ignore[assignment]

try:
    from PIL import Image, ImageDraw
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Pillow is required for image cropping. Install with: pip install pillow"
    ) from exc


@dataclass
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    score: float
    label: str
    window_index: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "x1": self.x1,
            "y1": self.y1,
            "x2": self.x2,
            "y2": self.y2,
            "score": self.score,
            "label": self.label,
            "window_index": self.window_index,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sliding-window image batching client for RunPod endpoints."
    )
    parser.add_argument("--image", type=Path, required=True, help="Path to input image.")
    parser.add_argument("--text", type=str, required=True, help="Prompt text for the model.")
    parser.add_argument(
        "--endpoint-id",
        type=str,
        default="q0qjd5mvmftv2t",
        help="RunPod endpoint id.",
    )
    parser.add_argument(
        "--api-key-env",
        type=str,
        default="RUNPOD",
        help="Environment variable that stores your RunPod API key.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Optional .env file path for API key loading.",
    )
    parser.add_argument(
        "--window-width",
        type=int,
        default=1024,
        help="Sliding-window width in pixels.",
    )
    parser.add_argument(
        "--window-height",
        type=int,
        default=1024,
        help="Sliding-window height in pixels.",
    )
    parser.add_argument(
        "--window-fraction",
        type=float,
        default=None,
        help="Fraction of original image size used for both width and height (0,1].",
    )
    parser.add_argument(
        "--window-width-fraction",
        type=float,
        default=None,
        help="Fraction of original image width used for window width (0,1].",
    )
    parser.add_argument(
        "--window-height-fraction",
        type=float,
        default=None,
        help="Fraction of original image height used for window height (0,1].",
    )
    parser.add_argument(
        "--min-window-width",
        type=int,
        default=512,
        help="Minimum window width in pixels when using fraction-based sizing.",
    )
    parser.add_argument(
        "--min-window-height",
        type=int,
        default=512,
        help="Minimum window height in pixels when using fraction-based sizing.",
    )
    parser.add_argument(
        "--stride-x",
        type=int,
        default=768,
        help="Horizontal stride in pixels.",
    )
    parser.add_argument(
        "--stride-y",
        type=int,
        default=768,
        help="Vertical stride in pixels.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="How many window crops to send in a single request.",
    )
    parser.add_argument(
        "--box-threshold",
        type=float,
        default=0.25,
        help="Model box threshold.",
    )
    parser.add_argument(
        "--text-threshold",
        type=float,
        default=0.25,
        help="Model text threshold.",
    )
    parser.add_argument(
        "--image-key",
        type=str,
        default="images_b64",
        help="Input field name used for the list of base64 crop images.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Use /runsync instead of /run.",
    )
    parser.add_argument(
        "--request-timeout",
        type=int,
        default=120,
        help="Request timeout in seconds.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.5,
        help="Polling interval in seconds for async RunPod jobs.",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=900,
        help="Max polling time in seconds per batch job.",
    )
    parser.add_argument(
        "--crop-format",
        type=str,
        default="PNG",
        choices=["PNG", "JPEG", "WEBP"],
        help="Format used when encoding each crop to base64.",
    )
    parser.add_argument(
        "--box-format",
        type=str,
        default="auto",
        choices=["auto", "xyxy", "xywh", "cxcywh"],
        help="How to interpret returned box values.",
    )
    parser.add_argument(
        "--normalized-boxes",
        type=str,
        default="auto",
        choices=["auto", "true", "false"],
        help="Whether model returns box coordinates normalized to [0,1].",
    )
    parser.add_argument(
        "--nms-iou-threshold",
        type=float,
        default=0.5,
        help="IoU threshold for non-max suppression merging.",
    )
    parser.add_argument(
        "--disable-nms",
        action="store_true",
        help="Disable NMS deduplication across overlapping windows.",
    )
    parser.add_argument(
        "--class-agnostic-nms",
        action="store_true",
        help="Run NMS across all labels instead of per label.",
    )
    parser.add_argument(
        "--overlay-output",
        type=Path,
        default=None,
        help="Output image path with merged detection overlays.",
    )
    parser.add_argument(
        "--detections-output",
        type=Path,
        default=None,
        help="Optional path to write merged detections JSON.",
    )
    parser.add_argument(
        "--line-width",
        type=int,
        default=3,
        help="Bounding box line width for overlay rendering.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path to write per-batch request/polling responses.",
    )
    return parser.parse_args()


def window_starts(total: int, window: int, stride: int) -> list[int]:
    if total <= 0:
        return [0]
    if window >= total:
        return [0]

    starts = list(range(0, total - window + 1, stride))
    last_start = total - window
    if starts[-1] != last_start:
        starts.append(last_start)
    return starts


def generate_windows(
    width: int,
    height: int,
    window_width: int,
    window_height: int,
    stride_x: int,
    stride_y: int,
) -> list[tuple[int, int, int, int]]:
    xs = window_starts(width, window_width, stride_x)
    ys = window_starts(height, window_height, stride_y)
    windows: list[tuple[int, int, int, int]] = []
    for y in ys:
        for x in xs:
            right = min(x + window_width, width)
            bottom = min(y + window_height, height)
            windows.append((x, y, right, bottom))
    return windows


def crop_to_b64(image: Image.Image, box: tuple[int, int, int, int], fmt: str) -> str:
    crop = image.crop(box)
    buf = io.BytesIO()
    crop.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def validate_positive(name: str, value: int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def to_float(value: Any) -> float | None:
    if is_number(value):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def load_env_fallback(env_file: Path) -> None:
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def validate_fraction(name: str, value: float | None) -> None:
    if value is None:
        return
    if not (0.0 < value <= 1.0):
        raise ValueError(f"{name} must be in (0, 1], got {value}")


def resolve_window_size(
    image_width: int,
    image_height: int,
    args: argparse.Namespace,
) -> tuple[int, int]:
    width_fraction = args.window_fraction
    height_fraction = args.window_fraction
    if args.window_width_fraction is not None:
        width_fraction = args.window_width_fraction
    if args.window_height_fraction is not None:
        height_fraction = args.window_height_fraction

    window_width = args.window_width
    if width_fraction is not None:
        window_width = int(round(image_width * width_fraction))
        window_width = max(args.min_window_width, window_width)

    window_height = args.window_height
    if height_fraction is not None:
        window_height = int(round(image_height * height_fraction))
        window_height = max(args.min_window_height, window_height)

    window_width = max(1, min(window_width, image_width))
    window_height = max(1, min(window_height, image_height))
    return window_width, window_height


def poll_job_status(
    endpoint_id: str,
    job_id: str,
    headers: dict[str, str],
    poll_interval: float,
    poll_timeout: int,
    request_timeout: int,
) -> dict[str, Any]:
    deadline = time.time() + poll_timeout
    status_url = f"https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}"
    while True:
        status_response = requests.get(status_url, headers=headers, timeout=request_timeout)
        try:
            status_json: dict[str, Any] = status_response.json()
        except ValueError:
            status_json = {"raw_text": status_response.text}

        if not status_response.ok:
            raise RuntimeError(
                f"Status polling failed ({status_response.status_code}): {status_json}"
            )

        state = str(status_json.get("status", "")).upper()
        if state in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            return status_json

        if time.time() >= deadline:
            raise TimeoutError(f"Polling timed out after {poll_timeout}s for job {job_id}")

        time.sleep(poll_interval)


def pick_first(mapping: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def infer_box_format_from_dict(box: dict[str, Any]) -> str:
    if {"x1", "y1", "x2", "y2"}.issubset(box):
        return "xyxy"
    if {"xmin", "ymin", "xmax", "ymax"}.issubset(box):
        return "xyxy"
    if {"left", "top", "right", "bottom"}.issubset(box):
        return "xyxy"
    if {"cx", "cy", "w", "h"}.issubset(box):
        return "cxcywh"
    if {"x_center", "y_center", "w", "h"}.issubset(box):
        return "cxcywh"
    if {"x", "y", "w", "h"}.issubset(box):
        return "xywh"
    if {"x", "y", "width", "height"}.issubset(box):
        return "xywh"
    return "xyxy"


def unpack_box_values(raw_box: Any) -> tuple[list[float], str] | None:
    if isinstance(raw_box, (list, tuple)) and len(raw_box) == 4:
        values = [to_float(v) for v in raw_box]
        if all(v is not None for v in values):
            return [float(v) for v in values], "xyxy"
        return None

    if isinstance(raw_box, dict):
        fmt = infer_box_format_from_dict(raw_box)
        if fmt == "xyxy":
            if {"x1", "y1", "x2", "y2"}.issubset(raw_box):
                vals = [raw_box["x1"], raw_box["y1"], raw_box["x2"], raw_box["y2"]]
            elif {"xmin", "ymin", "xmax", "ymax"}.issubset(raw_box):
                vals = [raw_box["xmin"], raw_box["ymin"], raw_box["xmax"], raw_box["ymax"]]
            elif {"left", "top", "right", "bottom"}.issubset(raw_box):
                vals = [raw_box["left"], raw_box["top"], raw_box["right"], raw_box["bottom"]]
            else:
                return None
        elif fmt == "xywh":
            if {"x", "y", "w", "h"}.issubset(raw_box):
                vals = [raw_box["x"], raw_box["y"], raw_box["w"], raw_box["h"]]
            elif {"x", "y", "width", "height"}.issubset(raw_box):
                vals = [raw_box["x"], raw_box["y"], raw_box["width"], raw_box["height"]]
            else:
                return None
        else:
            if {"cx", "cy", "w", "h"}.issubset(raw_box):
                vals = [raw_box["cx"], raw_box["cy"], raw_box["w"], raw_box["h"]]
            elif {"x_center", "y_center", "w", "h"}.issubset(raw_box):
                vals = [raw_box["x_center"], raw_box["y_center"], raw_box["w"], raw_box["h"]]
            else:
                return None

        values = [to_float(v) for v in vals]
        if all(v is not None for v in values):
            return [float(v) for v in values], fmt
    return None


def normalize_and_convert_box(
    raw_box: Any,
    window_w: int,
    window_h: int,
    box_format: str,
    normalized_boxes: str,
) -> tuple[float, float, float, float] | None:
    unpacked = unpack_box_values(raw_box)
    if unpacked is None:
        return None

    values, inferred_format = unpacked
    fmt = inferred_format if box_format == "auto" else box_format

    if normalized_boxes == "true":
        is_normalized = True
    elif normalized_boxes == "false":
        is_normalized = False
    else:
        is_normalized = max(abs(v) for v in values) <= 1.5

    x0, y0, x1, y1 = values
    if is_normalized:
        x0 *= window_w
        y0 *= window_h
        x1 *= window_w
        y1 *= window_h

    if fmt == "xywh":
        x1 = x0 + x1
        y1 = y0 + y1
    elif fmt == "cxcywh":
        cx = x0
        cy = y0
        w = x1
        h = y1
        x0 = cx - (w / 2.0)
        y0 = cy - (h / 2.0)
        x1 = cx + (w / 2.0)
        y1 = cy + (h / 2.0)

    left = min(x0, x1)
    top = min(y0, y1)
    right = max(x0, x1)
    bottom = max(y0, y1)

    left = clamp(left, 0.0, float(window_w))
    top = clamp(top, 0.0, float(window_h))
    right = clamp(right, 0.0, float(window_w))
    bottom = clamp(bottom, 0.0, float(window_h))

    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def group_by_image_index(items: Any, expected_windows: int) -> list[Any] | None:
    if not isinstance(items, list) or not items:
        return None
    if not all(isinstance(item, dict) for item in items):
        return None

    index_keys = ("image_index", "window_index", "image_id", "idx")
    has_index = any(any(key in item for key in index_keys) for item in items)
    if not has_index:
        return None

    grouped: list[list[dict[str, Any]]] = [[] for _ in range(expected_windows)]
    for item in items:
        idx_val = None
        for key in index_keys:
            if key in item:
                idx_val = item[key]
                break
        idx_f = to_float(idx_val)
        if idx_f is None:
            continue
        idx = int(idx_f)
        target_idx = None
        if 0 <= idx < expected_windows:
            target_idx = idx
        elif 1 <= idx <= expected_windows:
            target_idx = idx - 1
        if target_idx is not None:
            grouped[target_idx].append(item)
    return grouped


def split_output_per_window(output: Any, expected_windows: int) -> list[Any]:
    if expected_windows <= 0:
        return []
    if output is None:
        return [None for _ in range(expected_windows)]

    if isinstance(output, dict):
        list_keys = [
            "predictions",
            "results",
            "outputs",
            "detections_per_image",
            "images",
            "items",
            "data",
        ]
        for key in list_keys:
            value = output.get(key)
            if isinstance(value, list) and len(value) == expected_windows:
                return value

        for key in ("detections", "predictions", "results", "objects"):
            grouped = group_by_image_index(output.get(key), expected_windows)
            if grouped is not None:
                return grouped

        if expected_windows == 1:
            return [output]
        return [output] + [None for _ in range(expected_windows - 1)]

    if isinstance(output, list):
        grouped = group_by_image_index(output, expected_windows)
        if grouped is not None:
            return grouped
        if len(output) == expected_windows:
            return output
        if expected_windows == 1:
            return [output]
        return [output] + [None for _ in range(expected_windows - 1)]

    if expected_windows == 1:
        return [output]
    return [output] + [None for _ in range(expected_windows - 1)]


def parse_score(value: Any) -> float:
    score = to_float(value)
    if score is None:
        return 1.0
    return float(score)


def parse_label(value: Any) -> str:
    if value is None:
        return "object"
    if isinstance(value, str):
        return value.strip() or "object"
    return str(value)


def parse_detection_dict(
    item: dict[str, Any],
    window_w: int,
    window_h: int,
    box_format: str,
    normalized_boxes: str,
    window_index: int,
) -> Detection | None:
    box_candidate_keys = [
        "box_px",
        "box_abs",
        "box_xyxy",
        "box_norm",
        "box",
        "bbox",
        "xyxy",
        "coordinates",
        "rect",
    ]
    raw_box = None
    for key in box_candidate_keys:
        if key in item:
            raw_box = item[key]
            break

    if raw_box is None:
        raw_box = item

    box = normalize_and_convert_box(
        raw_box=raw_box,
        window_w=window_w,
        window_h=window_h,
        box_format=box_format,
        normalized_boxes=normalized_boxes,
    )
    if box is None:
        return None

    score = parse_score(
        pick_first(item, ["score", "confidence", "conf", "probability", "logit"])
    )
    label = parse_label(
        pick_first(item, ["label", "phrase", "class", "class_name", "text"])
    )
    return Detection(
        x1=box[0],
        y1=box[1],
        x2=box[2],
        y2=box[3],
        score=score,
        label=label,
        window_index=window_index,
    )


def parse_detections(
    raw: Any,
    window_w: int,
    window_h: int,
    box_format: str,
    normalized_boxes: str,
    window_index: int,
) -> list[Detection]:
    if raw is None:
        return []

    if isinstance(raw, dict):
        boxes = pick_first(raw, ["boxes", "bboxes"])
        if isinstance(boxes, list):
            scores = pick_first(raw, ["scores", "confidences", "logits", "probs"])
            labels = pick_first(raw, ["labels", "phrases", "classes", "class_names"])
            detections: list[Detection] = []
            for i, box_item in enumerate(boxes):
                box = normalize_and_convert_box(
                    raw_box=box_item,
                    window_w=window_w,
                    window_h=window_h,
                    box_format=box_format,
                    normalized_boxes=normalized_boxes,
                )
                if box is None:
                    continue
                score = 1.0
                if isinstance(scores, list) and i < len(scores):
                    score = parse_score(scores[i])
                label = "object"
                if isinstance(labels, list) and i < len(labels):
                    label = parse_label(labels[i])
                detections.append(
                    Detection(
                        x1=box[0],
                        y1=box[1],
                        x2=box[2],
                        y2=box[3],
                        score=score,
                        label=label,
                        window_index=window_index,
                    )
                )
            return detections

        for key in ("detections", "predictions", "results", "objects", "items", "output"):
            if key in raw:
                nested = parse_detections(
                    raw=raw[key],
                    window_w=window_w,
                    window_h=window_h,
                    box_format=box_format,
                    normalized_boxes=normalized_boxes,
                    window_index=window_index,
                )
                if nested:
                    return nested

        single = parse_detection_dict(
            item=raw,
            window_w=window_w,
            window_h=window_h,
            box_format=box_format,
            normalized_boxes=normalized_boxes,
            window_index=window_index,
        )
        return [single] if single is not None else []

    if isinstance(raw, list):
        if len(raw) == 4 and all(is_number(v) for v in raw):
            box = normalize_and_convert_box(
                raw_box=raw,
                window_w=window_w,
                window_h=window_h,
                box_format=box_format,
                normalized_boxes=normalized_boxes,
            )
            if box is None:
                return []
            return [
                Detection(
                    x1=box[0],
                    y1=box[1],
                    x2=box[2],
                    y2=box[3],
                    score=1.0,
                    label="object",
                    window_index=window_index,
                )
            ]

        detections: list[Detection] = []
        for item in raw:
            detections.extend(
                parse_detections(
                    raw=item,
                    window_w=window_w,
                    window_h=window_h,
                    box_format=box_format,
                    normalized_boxes=normalized_boxes,
                    window_index=window_index,
                )
            )
        return detections

    return []


def to_global_detection(
    det: Detection,
    window_box: tuple[int, int, int, int],
    image_w: int,
    image_h: int,
) -> Detection:
    x_off, y_off, _, _ = window_box
    x1 = clamp(det.x1 + x_off, 0.0, float(image_w))
    y1 = clamp(det.y1 + y_off, 0.0, float(image_h))
    x2 = clamp(det.x2 + x_off, 0.0, float(image_w))
    y2 = clamp(det.y2 + y_off, 0.0, float(image_h))
    return Detection(
        x1=min(x1, x2),
        y1=min(y1, y2),
        x2=max(x1, x2),
        y2=max(y1, y2),
        score=det.score,
        label=det.label,
        window_index=det.window_index,
    )


def iou(a: Detection, b: Detection) -> float:
    ix1 = max(a.x1, b.x1)
    iy1 = max(a.y1, b.y1)
    ix2 = min(a.x2, b.x2)
    iy2 = min(a.y2, b.y2)
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    intersection = iw * ih
    if intersection <= 0.0:
        return 0.0
    area_a = max(0.0, a.x2 - a.x1) * max(0.0, a.y2 - a.y1)
    area_b = max(0.0, b.x2 - b.x1) * max(0.0, b.y2 - b.y1)
    union = area_a + area_b - intersection
    if union <= 0.0:
        return 0.0
    return intersection / union


def non_max_suppression(
    detections: list[Detection],
    iou_threshold: float,
    class_agnostic: bool,
) -> list[Detection]:
    if not detections:
        return []

    grouped: dict[str, list[Detection]] = {}
    for det in detections:
        key = "__all__" if class_agnostic else det.label
        grouped.setdefault(key, []).append(det)

    merged: list[Detection] = []
    for dets in grouped.values():
        remaining = sorted(dets, key=lambda d: d.score, reverse=True)
        while remaining:
            best = remaining.pop(0)
            merged.append(best)
            kept: list[Detection] = []
            for candidate in remaining:
                if iou(best, candidate) <= iou_threshold:
                    kept.append(candidate)
            remaining = kept
    return merged


def color_for_label(label: str) -> tuple[int, int, int]:
    digest = hashlib.md5(label.encode("utf-8")).digest()
    return (55 + (digest[0] % 200), 55 + (digest[1] % 200), 55 + (digest[2] % 200))


def render_overlay(
    image: Image.Image,
    detections: list[Detection],
    output_path: Path,
    line_width: int,
) -> None:
    canvas = image.copy().convert("RGB")
    draw = ImageDraw.Draw(canvas)
    for det in detections:
        color = color_for_label(det.label)
        x1 = int(round(det.x1))
        y1 = int(round(det.y1))
        x2 = int(round(det.x2))
        y2 = int(round(det.y2))
        draw.rectangle([x1, y1, x2, y2], outline=color, width=line_width)
        caption = f"{det.label} {det.score:.2f}"
        try:
            text_box = draw.textbbox((x1, y1), caption)
            tx1, ty1, tx2, ty2 = text_box
        except AttributeError:
            text_w, text_h = draw.textsize(caption)  # type: ignore[attr-defined]
            tx1, ty1, tx2, ty2 = x1, y1, x1 + text_w, y1 + text_h
        draw.rectangle([tx1, ty1, tx2 + 4, ty2 + 2], fill=color)
        draw.text((tx1 + 2, ty1 + 1), caption, fill=(0, 0, 0))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def main() -> int:
    args = parse_args()
    if load_dotenv is not None:
        load_dotenv(dotenv_path=args.env_file if args.env_file else None)
    else:
        load_env_fallback(args.env_file)

    api_key = os.getenv(args.api_key_env) or os.getenv("RUNPOD_API_KEY")
    if not api_key:
        print(
            f"Missing API key. Set {args.api_key_env} in .env or environment.",
            file=sys.stderr,
        )
        return 1

    if not args.image.is_file():
        print(f"Image does not exist: {args.image}", file=sys.stderr)
        return 1

    try:
        validate_positive("window-width", args.window_width)
        validate_positive("window-height", args.window_height)
        validate_positive("min-window-width", args.min_window_width)
        validate_positive("min-window-height", args.min_window_height)
        validate_positive("stride-x", args.stride_x)
        validate_positive("stride-y", args.stride_y)
        validate_positive("batch-size", args.batch_size)
        validate_positive("request-timeout", args.request_timeout)
        validate_positive("poll-timeout", args.poll_timeout)
        validate_positive("line-width", args.line_width)
        validate_fraction("window-fraction", args.window_fraction)
        validate_fraction("window-width-fraction", args.window_width_fraction)
        validate_fraction("window-height-fraction", args.window_height_fraction)
        if args.poll_interval <= 0:
            raise ValueError("poll-interval must be > 0")
        if not (0.0 <= args.nms_iou_threshold <= 1.0):
            raise ValueError("nms-iou-threshold must be between 0 and 1")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    with Image.open(args.image) as img:
        image = img.convert("RGB")
        width, height = image.size
        effective_window_width, effective_window_height = resolve_window_size(
            image_width=width,
            image_height=height,
            args=args,
        )
        effective_stride_x = min(args.stride_x, effective_window_width)
        effective_stride_y = min(args.stride_y, effective_window_height)
        if effective_stride_x != args.stride_x or effective_stride_y != args.stride_y:
            print(
                "Adjusted stride to avoid gaps: "
                f"{args.stride_x}x{args.stride_y} -> {effective_stride_x}x{effective_stride_y}"
            )
        windows = generate_windows(
            width=width,
            height=height,
            window_width=effective_window_width,
            window_height=effective_window_height,
            stride_x=effective_stride_x,
            stride_y=effective_stride_y,
        )

        print(f"Image size: {width}x{height}")
        print(f"Window size: {effective_window_width}x{effective_window_height}")
        print(f"Stride: {effective_stride_x}x{effective_stride_y}")
        print(f"Total windows: {len(windows)}")
        print(f"Batch size: {args.batch_size}")

        endpoint_path = "runsync" if args.sync else "run"
        url = f"https://api.runpod.ai/v2/{args.endpoint_id}/{endpoint_path}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        batch_records: list[dict[str, Any]] = []
        merged_global_detections: list[Detection] = []
        total_batches = (len(windows) + args.batch_size - 1) // args.batch_size if windows else 0

        for batch_idx, start in enumerate(range(0, len(windows), args.batch_size), start=1):
            window_batch = windows[start : start + args.batch_size]
            images_b64 = [crop_to_b64(image, box, args.crop_format) for box in window_batch]
            payload = {
                "input": {
                    args.image_key: images_b64,
                    "text": args.text,
                    "box_threshold": args.box_threshold,
                    "text_threshold": args.text_threshold,
                }
            }

            print(f"[{batch_idx}/{total_batches}] Sending {len(window_batch)} windows...")
            submit_response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=args.request_timeout,
            )
            try:
                submit_json: Any = submit_response.json()
            except ValueError:
                submit_json = {"raw_text": submit_response.text}

            if not submit_response.ok:
                print(f"[{batch_idx}/{total_batches}] ERROR ({submit_response.status_code})")
                print(json.dumps(submit_json, indent=2))
                batch_records.append(
                    {
                        "batch_index": batch_idx,
                        "status_code": submit_response.status_code,
                        "ok": False,
                        "window_count": len(window_batch),
                        "window_coords": [
                            {"x": l, "y": t, "w": r - l, "h": b - t}
                            for (l, t, r, b) in window_batch
                        ],
                        "submit_response": submit_json,
                        "status_response": None,
                        "final_status": "SUBMIT_FAILED",
                        "output": None,
                    }
                )
                continue

            final_status = "COMPLETED"
            final_output: Any = None
            status_json: Any = None
            job_id = None

            if args.sync:
                final_output = submit_json.get("output", submit_json)
                print(f"[{batch_idx}/{total_batches}] OK ({submit_response.status_code}) [sync]")
            else:
                job_id = submit_json.get("id")
                if not job_id:
                    final_status = "MISSING_JOB_ID"
                    final_output = submit_json.get("output")
                    print(f"[{batch_idx}/{total_batches}] ERROR (missing job id)")
                else:
                    try:
                        status_json = poll_job_status(
                            endpoint_id=args.endpoint_id,
                            job_id=str(job_id),
                            headers=headers,
                            poll_interval=args.poll_interval,
                            poll_timeout=args.poll_timeout,
                            request_timeout=args.request_timeout,
                        )
                        final_status = str(status_json.get("status", "UNKNOWN")).upper()
                        final_output = status_json.get("output")
                        print(
                            f"[{batch_idx}/{total_batches}] status={final_status} job_id={job_id}"
                        )
                    except Exception as exc:
                        final_status = "POLLING_ERROR"
                        final_output = {"error": str(exc)}
                        print(f"[{batch_idx}/{total_batches}] ERROR while polling: {exc}")

            per_window_output = split_output_per_window(final_output, len(window_batch))
            local_detection_count = 0
            for local_idx, raw_window_output in enumerate(per_window_output):
                current_window = window_batch[local_idx]
                window_w = current_window[2] - current_window[0]
                window_h = current_window[3] - current_window[1]
                window_index = start + local_idx
                local_dets = parse_detections(
                    raw=raw_window_output,
                    window_w=window_w,
                    window_h=window_h,
                    box_format=args.box_format,
                    normalized_boxes=args.normalized_boxes,
                    window_index=window_index,
                )
                local_detection_count += len(local_dets)
                for det in local_dets:
                    merged_global_detections.append(
                        to_global_detection(
                            det=det,
                            window_box=current_window,
                            image_w=width,
                            image_h=height,
                        )
                    )

            batch_records.append(
                {
                    "batch_index": batch_idx,
                    "status_code": submit_response.status_code,
                    "ok": final_status == "COMPLETED",
                    "job_id": job_id,
                    "window_count": len(window_batch),
                    "window_coords": [
                        {"x": l, "y": t, "w": r - l, "h": b - t}
                        for (l, t, r, b) in window_batch
                    ],
                    "submit_response": submit_json,
                    "status_response": status_json,
                    "final_status": final_status,
                    "detection_count_local": local_detection_count,
                    "output": final_output,
                }
            )

        if args.output_json:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(json.dumps(batch_records, indent=2), encoding="utf-8")
            print(f"Saved batch records: {args.output_json}")

        raw_count = len(merged_global_detections)
        if args.disable_nms:
            merged = merged_global_detections
        else:
            merged = non_max_suppression(
                detections=merged_global_detections,
                iou_threshold=args.nms_iou_threshold,
                class_agnostic=args.class_agnostic_nms,
            )
        print(f"Detections before merge: {raw_count}")
        print(f"Detections after merge: {len(merged)}")

        detections_path = args.detections_output
        if detections_path is None:
            detections_path = args.image.with_name(f"{args.image.stem}_detections.json")
        detections_path.parent.mkdir(parents=True, exist_ok=True)
        detections_path.write_text(
            json.dumps([det.as_dict() for det in merged], indent=2),
            encoding="utf-8",
        )
        print(f"Saved merged detections: {detections_path}")

        overlay_path = args.overlay_output
        if overlay_path is None:
            overlay_path = args.image.with_name(f"{args.image.stem}_overlay.png")
        render_overlay(
            image=image,
            detections=merged,
            output_path=overlay_path,
            line_width=args.line_width,
        )
        print(f"Saved overlay image: {overlay_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
