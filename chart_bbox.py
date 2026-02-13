#!/usr/bin/env python3
"""Generate chart-region polygon overlay using PPStructureV3.

Default run:
  python chart_bbox.py
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from PIL import Image, ImageDraw
from paddleocr import PPStructureV3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "image",
        nargs="?",
        default="/home/javad/NeuroNote_Charts/manim_drawing_charts/input_images/multiple_pie2.png",
        help="Input image path",
    )
    parser.add_argument(
        "--overlay",
        default=None,
        help="Output overlay image path (default: <image_stem>_chart_polygons_overlay.png)",
    )
    parser.add_argument(
        "--json",
        default=None,
        help="Output chart polygon JSON path (default: <image_stem>_chart_polygons.json)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    image_path = Path(args.image).expanduser().resolve()
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    overlay_path = (
        Path(args.overlay).expanduser().resolve()
        if args.overlay
        else image_path.with_name(f"{image_path.stem}_chart_polygons_overlay.png")
    )
    json_path = (
        Path(args.json).expanduser().resolve()
        if args.json
        else image_path.with_name(f"{image_path.stem}_chart_polygons.json")
    )

    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    # Skip slow connectivity checks.
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

    pipeline = PPStructureV3(
        use_table_recognition=False,
        use_formula_recognition=False,
        use_chart_recognition=False,
        use_seal_recognition=False,
    )

    res = next(iter(pipeline.predict(str(image_path))))
    root = (getattr(res, "json", {}) or {}).get("res", getattr(res, "json", {}) or {})
    layout = root.get("layout_det_res", {}) if isinstance(root, dict) else {}
    boxes = layout.get("boxes", []) if isinstance(layout, dict) else []

    chart_polygons = []
    for b in boxes:
        if str(b.get("label", "")).lower() != "chart":
            continue
        coord = b.get("coordinate") or []
        if len(coord) < 4:
            continue

        x1, y1, x2, y2 = [float(v) for v in coord[:4]]
        polygon = [
            [round(x1, 3), round(y1, 3)],
            [round(x2, 3), round(y1, 3)],
            [round(x2, 3), round(y2, 3)],
            [round(x1, 3), round(y2, 3)],
        ]
        chart_polygons.append(
            {
                "label": "chart",
                "score": float(b.get("score")) if b.get("score") is not None else None,
                "polygon": polygon,
            }
        )

    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)

    for idx, chart in enumerate(chart_polygons, 1):
        pts = [(int(round(x)), int(round(y))) for x, y in chart["polygon"]]
        draw.polygon(pts, outline=(0, 255, 140), width=5)
        label = f"chart {idx} {chart['score']:.2f}" if chart["score"] is not None else f"chart {idx}"
        draw.text((pts[0][0] + 6, max(4, pts[0][1] - 20)), label, fill=(255, 255, 255))

    image.save(overlay_path)

    payload = {
        "image": str(image_path),
        "chart_polygons": chart_polygons,
    }
    json_path.write_text(json.dumps(payload, indent=2))

    print(f"overlay: {overlay_path}")
    print(f"json: {json_path}")
    print(f"chart_polygons: {len(chart_polygons)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
