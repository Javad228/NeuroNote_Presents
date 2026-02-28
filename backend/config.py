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
    openai_api_key: str
    openai_base_url: str | None
    jobs_root: Path
    default_render_dpi: int
    neuronote_timeout_seconds: float
    neuronote_poll_interval_seconds: float
    qa_openai_timeout_seconds: float
    qa_embed_model: str
    qa_select_model: str
    qa_answer_model: str
    qa_enable_query_rewrite: bool
    qa_enable_llm_rerank: bool
    qa_enable_answerability_gate: bool
    qa_enable_verifier: bool
    qa_query_rewrite_count: int
    qa_retrieve_top_k_per_query: int
    qa_merged_candidate_cap: int
    qa_rerank_candidates: int
    qa_rerank_top_n: int
    qa_rewrite_model: str
    qa_rerank_model: str
    qa_gate_model: str
    qa_verify_model: str
    qa_default_top_k: int
    qa_default_max_selected_units: int
    max_pdf_size_mb: int
    azure_ocr_level: str
    azure_ocr_min_conf: float
    cors_allow_origins: list[str]
    gcs_images_bucket: str
    gcs_images_prefix: str


def _parse_cors_allow_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return default


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
    openai_base_url_raw = os.environ.get("OPENAI_BASE_URL", "").strip()
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
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        openai_base_url=openai_base_url_raw.rstrip("/") if openai_base_url_raw else None,
        jobs_root=Path(os.environ.get("JOBS_ROOT", str(base_dir / "jobs"))).resolve(),
        default_render_dpi=int(os.environ.get("RENDER_DPI", "200")),
        neuronote_timeout_seconds=float(os.environ.get("NEURONOTE_TIMEOUT_SECONDS", "3600")),
        neuronote_poll_interval_seconds=float(os.environ.get("NEURONOTE_POLL_INTERVAL_SECONDS", "2.0")),
        qa_openai_timeout_seconds=float(os.environ.get("QA_OPENAI_TIMEOUT_SECONDS", "60")),
        qa_embed_model=os.environ.get("QA_EMBED_MODEL", "text-embedding-3-small").strip(),
        qa_select_model=os.environ.get("QA_SELECT_MODEL", "gpt-5-mini").strip(),
        qa_answer_model=os.environ.get("QA_ANSWER_MODEL", "gpt-5").strip(),
        qa_enable_query_rewrite=_parse_bool(os.environ.get("QA_ENABLE_QUERY_REWRITE"), True),
        qa_enable_llm_rerank=_parse_bool(os.environ.get("QA_ENABLE_LLM_RERANK"), True),
        qa_enable_answerability_gate=_parse_bool(os.environ.get("QA_ENABLE_ANSWERABILITY_GATE"), True),
        qa_enable_verifier=_parse_bool(os.environ.get("QA_ENABLE_VERIFIER"), True),
        qa_query_rewrite_count=int(os.environ.get("QA_QUERY_REWRITE_COUNT", "2")),
        qa_retrieve_top_k_per_query=int(os.environ.get("QA_RETRIEVE_TOP_K_PER_QUERY", "24")),
        qa_merged_candidate_cap=int(os.environ.get("QA_MERGED_CANDIDATE_CAP", "30")),
        qa_rerank_candidates=int(os.environ.get("QA_RERANK_CANDIDATES", "20")),
        qa_rerank_top_n=int(os.environ.get("QA_RERANK_TOP_N", "8")),
        qa_rewrite_model=os.environ.get("QA_REWRITE_MODEL", "").strip()
        or os.environ.get("QA_SELECT_MODEL", "gpt-5-mini").strip(),
        qa_rerank_model=os.environ.get("QA_RERANK_MODEL", "").strip()
        or os.environ.get("QA_SELECT_MODEL", "gpt-5-mini").strip(),
        qa_gate_model=os.environ.get("QA_GATE_MODEL", "").strip()
        or os.environ.get("QA_SELECT_MODEL", "gpt-5-mini").strip(),
        qa_verify_model=os.environ.get("QA_VERIFY_MODEL", "").strip()
        or os.environ.get("QA_SELECT_MODEL", "gpt-5-mini").strip(),
        qa_default_top_k=int(os.environ.get("QA_DEFAULT_TOP_K", "10")),
        qa_default_max_selected_units=int(os.environ.get("QA_DEFAULT_MAX_SELECTED_UNITS", "4")),
        max_pdf_size_mb=int(os.environ.get("MAX_PDF_SIZE_MB", "100")),
        azure_ocr_level=os.environ.get("AZURE_OCR_LEVEL", "lines"),
        azure_ocr_min_conf=float(os.environ.get("AZURE_OCR_MIN_CONF", "0.0")),
        cors_allow_origins=_parse_cors_allow_origins(
            os.environ.get("CORS_ALLOW_ORIGINS", "")
        ),
        gcs_images_bucket=os.environ.get("GCS_IMAGES_BUCKET", "lectura-images").strip(),
        gcs_images_prefix=os.environ.get("GCS_IMAGES_PREFIX", "").strip().strip("/"),
    )
