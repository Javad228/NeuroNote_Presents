import sys
from pathlib import Path
from typing import Callable


def build_azure_ocr_extractor(
    *,
    neuronote_pipeline_root: Path,
    azure_level: str,
    azure_min_conf: float,
) -> Callable[[Path], str]:
    """Build an OCR extractor that uses SlideParser's Azure OCR backend."""
    root_str = str(neuronote_pipeline_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    try:
        from neuronote.extraction.ocr.dispatch import load_ocr_engine, run_ocr  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Failed to import SlideParser OCR modules. "
            f"Expected package under: {neuronote_pipeline_root}"
        ) from exc

    engine = load_ocr_engine(backend="azure")

    def extract_text(image_path: Path) -> str:
        detections = run_ocr(
            str(image_path),
            engine,
            backend="azure",
            azure_level=azure_level,
            azure_min_conf=azure_min_conf,
        )

        if not detections:
            return ""

        items = []
        for det in detections:
            text = str(det.get("text_content", "")).strip()
            bbox = det.get("bbox") or [0, 0, 0, 0]
            if not text:
                continue
            try:
                x1, y1, x2, y2 = [float(v) for v in bbox]
            except Exception:
                x1, y1, x2, y2 = 0.0, 0.0, 0.0, 0.0
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            items.append((cy, cx, text))

        if not items:
            return ""

        items.sort(key=lambda value: (value[0], value[1]))
        return "\n".join(text for _, _, text in items)

    return extract_text


def install_chunking_ocr_override(
    *,
    chunking_root: Path,
    neuronote_pipeline_root: Path,
    azure_level: str,
    azure_min_conf: float,
) -> None:
    """Replace chunking repo OCR with Azure OCR implementation."""
    root_str = str(chunking_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    try:
        from src import ocr as chunking_ocr  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            f"Failed to import chunking OCR module from {chunking_root}"
        ) from exc

    extractor = build_azure_ocr_extractor(
        neuronote_pipeline_root=neuronote_pipeline_root,
        azure_level=azure_level,
        azure_min_conf=azure_min_conf,
    )

    chunking_ocr.extract_text_from_image = extractor
