import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    chunking_root: Path
    neuronote_pipeline_root: Path
    neuronote_api_base: str
    jobs_root: Path
    default_render_dpi: int
    neuronote_timeout_seconds: float
    max_pdf_size_mb: int
    azure_ocr_level: str
    azure_ocr_min_conf: float


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    base_dir = Path(__file__).resolve().parent.parent
    return AppConfig(
        chunking_root=Path(
            os.environ.get("CHUNKING_ROOT", "/home/javad/NeuroNote/chunking_slides_v2")
        ).resolve(),
        neuronote_pipeline_root=Path(
            os.environ.get("NEURONOTE_PIPELINE_ROOT", "/home/javad/NeuroNote_Pipeline")
        ).resolve(),
        neuronote_api_base=os.environ.get("NEURONOTE_API_BASE", "http://127.0.0.1:8000").rstrip("/"),
        jobs_root=Path(os.environ.get("JOBS_ROOT", str(base_dir / "jobs"))).resolve(),
        default_render_dpi=int(os.environ.get("RENDER_DPI", "200")),
        neuronote_timeout_seconds=float(os.environ.get("NEURONOTE_TIMEOUT_SECONDS", "3600")),
        max_pdf_size_mb=int(os.environ.get("MAX_PDF_SIZE_MB", "100")),
        azure_ocr_level=os.environ.get("AZURE_OCR_LEVEL", "lines"),
        azure_ocr_min_conf=float(os.environ.get("AZURE_OCR_MIN_CONF", "0.0")),
    )
