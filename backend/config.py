import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    chunking_root: Path
    neuronote_pipeline_root: Path
    neuronote_artifact_roots: list[Path]
    neuronote_api_base: str
    jobs_root: Path
    default_render_dpi: int
    neuronote_timeout_seconds: float
    neuronote_poll_interval_seconds: float
    max_pdf_size_mb: int
    azure_ocr_level: str
    azure_ocr_min_conf: float
    cors_allow_origins: list[str]
    gcs_images_bucket: str
    gcs_images_prefix: str


def _parse_cors_allow_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _parse_artifact_roots(raw: str) -> list[Path]:
    if not raw:
        return []

    # Accept comma-separated and os.pathsep-separated lists.
    tokens: list[str] = []
    for part in raw.split(","):
        tokens.extend(part.split(os.pathsep))

    roots: list[Path] = []
    seen: set[str] = set()
    for token in tokens:
        item = token.strip()
        if not item:
            continue
        try:
            resolved = Path(item).expanduser().resolve()
        except Exception:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)

    return roots


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
        neuronote_artifact_roots=_parse_artifact_roots(
            os.environ.get("NEURONOTE_ARTIFACT_ROOTS", "")
        ),
        neuronote_api_base=os.environ.get("NEURONOTE_API_BASE", "http://127.0.0.1:8000").rstrip("/"),
        jobs_root=Path(os.environ.get("JOBS_ROOT", str(base_dir / "jobs"))).resolve(),
        default_render_dpi=int(os.environ.get("RENDER_DPI", "200")),
        neuronote_timeout_seconds=float(os.environ.get("NEURONOTE_TIMEOUT_SECONDS", "3600")),
        neuronote_poll_interval_seconds=float(os.environ.get("NEURONOTE_POLL_INTERVAL_SECONDS", "2.0")),
        max_pdf_size_mb=int(os.environ.get("MAX_PDF_SIZE_MB", "100")),
        azure_ocr_level=os.environ.get("AZURE_OCR_LEVEL", "lines"),
        azure_ocr_min_conf=float(os.environ.get("AZURE_OCR_MIN_CONF", "0.0")),
        cors_allow_origins=_parse_cors_allow_origins(
            os.environ.get("CORS_ALLOW_ORIGINS", "")
        ),
        gcs_images_bucket=os.environ.get("GCS_IMAGES_BUCKET", "lectura-images").strip(),
        gcs_images_prefix=os.environ.get("GCS_IMAGES_PREFIX", "").strip().strip("/"),
    )
