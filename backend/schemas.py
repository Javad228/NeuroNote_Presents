from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class ProcessPdfOptions:
    method: str
    penalty: Optional[float]
    n_bkps: Optional[int]
    min_chunk: int
    use_embeddings: bool
    use_cache: bool
    skip_generation: bool
    previous_context: Optional[str]
    render_dpi: int


class QaAnswerRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    top_k: Optional[int] = Field(default=None, ge=1, le=50)
    max_selected_units: Optional[int] = Field(default=None, ge=1, le=10)


class QaHighlightRef(BaseModel):
    slide_id: str
    slide_number: int
    region_id: str


class QaAnswerLine(BaseModel):
    line_index: int
    text: str
    highlights: list[QaHighlightRef] = Field(default_factory=list)
    unit_ids: list[str] = Field(default_factory=list)


class QaUsedContext(BaseModel):
    selected_unit_ids: list[str] = Field(default_factory=list)
    expanded_unit_ids: list[str] = Field(default_factory=list)
    context_slide_ids: list[str] = Field(default_factory=list)


class QaTimingsMs(BaseModel):
    index_load_or_build: int
    question_embedding: int
    retrieval: int
    llm_select: int
    llm_answer: int
    total: int


class QaDebugTrace(BaseModel):
    retrieval_candidates: Optional[list[dict[str, Any]]] = None
    pass1_prompt_units: Optional[list[dict[str, Any]]] = None
    pass1_selected_unit_ids_raw: Optional[Any] = None
    pass2_prompt_unit_ids: Optional[list[str]] = None
    region_catalog_summary: Optional[dict[str, Any]] = None
    fallback_notes: Optional[list[str]] = None
    pipeline_version: Optional[str] = None
    query_variants: Optional[list[str]] = None
    query_rewrite_raw: Optional[Any] = None
    retrieval_merged_candidates: Optional[list[dict[str, Any]]] = None
    rerank_input_unit_ids: Optional[list[str]] = None
    rerank_output_unit_ids: Optional[list[str]] = None
    rerank_raw: Optional[Any] = None
    answerability_raw: Optional[Any] = None
    verification_raw: Optional[Any] = None
    verification_results: Optional[list[dict[str, Any]]] = None
    stage_timings_ms: Optional[dict[str, int]] = None


class QaAnswerResponse(BaseModel):
    job_id: str
    question: str
    answer_text: str
    answer_lines: list[QaAnswerLine] = Field(default_factory=list)
    used_context: QaUsedContext
    timings_ms: QaTimingsMs
    answerable: bool = True
    confidence: Optional[float] = None
    reason_codes: list[str] = Field(default_factory=list)
    pipeline_version: str = "v2"
    debug: Optional[QaDebugTrace] = None
