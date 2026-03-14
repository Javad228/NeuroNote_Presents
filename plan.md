# Q&A "SOTA Without Training" Plan (Implementable Now)

## Constraints (explicit)

This plan only includes changes that can be implemented now in this repo by code + dependencies:

- No model training or fine-tuning
- No custom model hosting required
- Can add Python dependencies
- Can use existing OpenAI APIs already used by the repo (embeddings + chat)
- Can use pre-trained local rerankers (inference only)

## What "SOTA" Means Here

Not leaderboard SOTA by training a new model.

It means a state-of-practice QA stack for this product, using strong retrieval + reranking + verification + evals so the system is measurably better than the current implementation.

## Current QA (Baseline Snapshot)

Current pipeline in:

- `backend/services/qa_index.py`
- `backend/services/question_answering.py`
- `backend/services/openai_qa.py`

Today it does:

- Per-step explanation-unit indexing
- One dense embedding per unit + BM25 tokens
- Hybrid retrieval (dense + BM25 + RRF)
- LLM unit selection
- Context expansion to neighbors
- LLM answer generation with structured JSON
- Structural validation of cited units/highlights

This is a solid baseline, but below state-of-practice because it lacks reranking, query expansion, semantic verification, answerability gating, and benchmark-driven iteration.

## The Implementable-Now SOTA Upgrade (No Training)

## Phase 0: Benchmark + Eval Harness (must happen first)

Goal: prove improvements with numbers, not prompt impressions.

### What to build now

- `evals/qa_benchmark/*.jsonl` dataset format
- `scripts/qa_eval.py` runner that calls the local QA endpoint or service directly
- JSON report output with retrieval and answer quality metrics
- A simple leaderboard file (baseline vs variants)

### Dataset schema (minimum)

Each eval example should include:

- `job_id`
- `question`
- `answerable` (bool)
- `gold_answer` (or rubric)
- `gold_unit_ids` (list)
- `gold_region_ids` (list, optional)
- `question_type` (`fact`, `explain`, `compare`, `visual`, `definition`, etc.)

### Metrics to track

- Retrieval Recall@k (`gold_unit_ids`)
- MRR / nDCG@k (optional but useful)
- Answer correctness (LLM judge rubric or manual)
- Faithfulness / groundedness (claims supported by cited units)
- Highlight accuracy (region-level precision/recall when labeled)
- Abstention precision/recall for unanswerable questions
- Latency p50/p95
- Cost/query (estimated)

### Repo touchpoints

- `backend/services/question_answering.py` (expose richer debug traces)
- `backend/schemas.py` (optional debug fields)
- New `scripts/qa_eval.py`
- New `evals/qa_benchmark/`

## Phase 1: Retrieval V2 (largest quality gain, still simple)

Goal: improve evidence recall before changing answer generation much.

### 1.1 Expand what gets indexed (using data you already have)

In `backend/services/qa_index.py`, index more than `explanation_text`.

Add text fields per unit/slide/region from existing lecture payload:

- `explanation_text` (already present)
- region `kind`
- region `description` / display text (already present when available)
- slide-local neighboring step text (prev/next)
- slide-level concatenated step text
- chunk-level concatenated slide text
- transcript snippets if available in job artifacts (optional but implementable)

Important: keep a stable `unit_id` and attach all extra fields as retrieval features, not as replacements.

### 1.2 Multi-field retrieval scoring (no training)

Instead of one dense score + one BM25 score:

- Compute BM25 on multiple corpora:
  - unit text
  - slide text
  - region descriptions
- Compute dense embeddings for multiple text views:
  - unit-only text
  - unit + region descriptors
  - slide summary text (derived from concatenated steps)
- Fuse scores with weighted RRF / weighted sum

This is implementable with deterministic scoring and benchmark tuning only.

### 1.3 Upgrade embedding defaults (no code risk, high impact)

Change default embedding model from `text-embedding-3-small` to `text-embedding-3-large` (feature flag + benchmark-verified).

Keep model configurable in `backend/config.py`.

### 1.4 Query rewriting and expansion (using existing OpenAI API)

Before retrieval, generate 2-4 alternate queries:

- abbreviation expansion
- synonym/term normalization
- "what slide mentions X and Y" style retrieval-focused rewrite
- optional decomposition for multi-part questions

Then retrieve for each query and merge with RRF.

This is often a major gain on lecture/Q&A phrasing mismatch and requires no training.

### 1.5 Add reranking (pre-trained inference only)

After retrieving top 30-50 candidates, rerank to top 8-12.

Preferred implementation paths:

- Local pre-trained cross-encoder (`sentence-transformers` CrossEncoder) on CPU/GPU
- Fallback: LLM rerank with current OpenAI chat model if local reranker is unavailable

No training is required; this is inference over candidate pairs.

### 1.6 Diversity-aware context packing

Replace "top-k then neighbor expansion only" with:

- MMR-style diversity selection across slides/chunks
- keep adjacent units only when they improve evidence continuity
- cap duplicate evidence from the same slide unless question is local

### Dependencies we can add now (Phase 1)

- `sentence-transformers`
- `torch` (or `onnxruntime` path later)
- `hnswlib` or `faiss-cpu` (optional for scale)
- `rapidfuzz` (query normalization / alias matching)
- `orjson` (faster index read/write, optional)

### Repo touchpoints

- `backend/services/qa_index.py`
- `backend/services/question_answering.py`
- `backend/services/openai_qa.py`
- `backend/config.py`

## Phase 2: Answerability + Better Structured Answering

Goal: stop confident bad answers and make responses easier to trust.

### 2.1 Add an answerability gate

Before final answer generation, run a lightweight pass:

- decide `answerable` vs `insufficient_evidence`
- require reference to retrieved evidence
- return a short abstention if evidence is weak

This can be done with the existing OpenAI chat flow and a strict schema.

### 2.2 Tighten output schema and validation

Current code validates structure and IDs. Extend it to require:

- `answerable` (bool)
- `answer_lines[]`
- per-line `unit_ids`
- per-line `highlights`
- `confidence` (heuristic bucket or 0-1 float)
- `reason_codes` (debug / optional)

Validate using Pydantic (already present) and reject malformed outputs with retry.

### 2.3 Claim-by-claim answering format

Have the model answer in small factual lines (already partly done), but explicitly enforce:

- one claim per line
- each claim cites at least one `unit_id`
- highlights only when there is clear visual grounding

This makes verification much easier.

### Repo touchpoints

- `backend/services/openai_qa.py`
- `backend/services/question_answering.py`
- `backend/schemas.py`

## Phase 3: Verification Pass (semantic grounding, no training)

Goal: move from "IDs exist" to "claims are actually supported".

### 3.1 Add a verifier stage after answer generation

For each answer line:

- provide the claim text
- provide cited `unit_ids` text
- ask verifier model: `supported`, `partially_supported`, or `unsupported`

Then:

- drop unsupported lines
- rewrite partially supported lines conservatively
- if too many lines are unsupported, return abstention

This is implementable today with the same OpenAI chat API.

### 3.2 Heuristic confidence (no calibration training)

Compute response confidence from deterministic features:

- retrieval score margin
- reranker score margin
- citation coverage (all lines cited?)
- verification results (supported ratio)
- answerability gate outcome

Use conservative bucketing (`low`, `medium`, `high`) if numeric confidence feels noisy.

### 3.3 Add explicit failure modes in the response/debug trace

Examples:

- `insufficient_retrieval_evidence`
- `verification_failed`
- `selector_disagreed_with_reranker`
- `schema_retry_used`

This makes eval and production debugging much faster.

### Repo touchpoints

- `backend/services/question_answering.py`
- `backend/services/openai_qa.py`
- `backend/schemas.py`

## Phase 4: Multimodal Improvements (optional but implementable, no training)

Goal: improve chart/diagram/table questions using existing slide images.

This phase is optional for first ship, but it is implementable without training if your configured API model supports vision.

### 4.1 Vision-assisted verification for visual questions

For questions detected as visual (graph/table/diagram):

- keep text-RAG retrieval as primary
- run a vision verification pass on the referenced slide image (and optional region crop)
- only confirm visual claims that are consistent with both text and image

### 4.2 Region crop generation

Use existing region coordinates/IDs (if present in lecture payload) to generate cropped images and attach them to verifier prompts.

If region coordinates are unavailable, use full slide image verification only.

### 4.3 Keep this behind a feature flag

- `QA_ENABLE_VISION_VERIFY`
- Route only `visual` question types to this path

### Repo touchpoints

- `backend/services/question_answering.py`
- `backend/services/openai_qa.py`
- `backend/services/lecture.py` / job artifact access utilities (for slide image paths)

## Phase 5: Production Hardening (still no training)

Goal: make the improved QA reliable and affordable in production.

### 5.1 Feature flags / pipeline versions

Add `v1` and `v2` pipeline routing in config:

- `QA_PIPELINE_VERSION`
- `QA_ENABLE_QUERY_REWRITE`
- `QA_ENABLE_RERANK`
- `QA_ENABLE_ANSWERABILITY_GATE`
- `QA_ENABLE_VERIFIER`
- `QA_ENABLE_VISION_VERIFY`

### 5.2 Caching

Implement caches for:

- question embeddings (keyed by normalized question + embed model)
- query rewrites
- rerank results (question + candidate set hash)
- final answer (job + normalized question + pipeline version)

### 5.3 Observability

Log per-stage timings and scores:

- retrieve
- rerank
- select/pack
- answerability
- answer
- verify

Add to debug payload so offline eval can reuse the same fields.

### 5.4 Regression checks

- Run benchmark before changing prompts/weights
- Store score deltas in CI or local script output
- Treat drops in faithfulness/abstention precision as blockers

## Concrete "SOTA-No-Training" Architecture for This Repo

Recommended v2 pipeline:

1. Query normalize + classify (`factual` / `explain` / `compare` / `visual`)
2. Generate query rewrites (2-4) via OpenAI
3. Hybrid multi-field retrieval for each rewrite (BM25 + dense)
4. Merge with RRF
5. Cross-encoder rerank top 40 -> top 10
6. MMR/diversity context packing + neighbor expansion
7. Answerability gate
8. Structured answer generation with citations/highlights
9. Semantic verification pass
10. Confidence + final response

This is implementable today with no training and is a major step up from the current two-pass approach.

## What I Can Implement First (highest ROI, lowest risk)

### Sprint 1 (immediate)

1. Add eval harness + benchmark format (`scripts/qa_eval.py`, `evals/qa_benchmark/`)
2. Upgrade embedding default and add config flags
3. Add query rewrite + multi-query retrieval merge (RRF)
4. Add rerank stage (cross-encoder, feature-flagged)
5. Add richer debug traces for retrieval/rerank scores

### Sprint 2

1. Add answerability gate
2. Tighten output schema (`answerable`, `confidence`, `reason_codes`)
3. Add semantic verifier pass
4. Add abstention path and confidence bucketing

### Sprint 3

1. Add multimodal/vision verification for visual questions (feature-flagged)
2. Add caches and benchmark regression script
3. Tune retrieval/rerank weights on benchmark (parameter tuning only, no training)

## Implementation Notes by File

### `backend/services/qa_index.py`

- Add index `v2` payload with multi-field texts and multiple embedding arrays
- Add retrieval scoring functions for multi-field dense + BM25 fusion
- Keep backward compatibility with `v1` index load path

### `backend/services/question_answering.py`

- Refactor to explicit stages (`rewrite`, `retrieve`, `rerank`, `pack`, `gate`, `answer`, `verify`)
- Add feature-flag routing and stage timings
- Add failure reason codes + debug traces

### `backend/services/openai_qa.py`

- Add methods for:
  - query rewrites
  - answerability gate
  - answer verifier
- Keep robust JSON parsing/retries

### `backend/config.py`

Add feature flags and tuning knobs:

- `QA_PIPELINE_VERSION`
- `QA_RETRIEVE_TOP_K`
- `QA_RERANK_CANDIDATES`
- `QA_RERANK_TOP_N`
- `QA_ENABLE_QUERY_REWRITE`
- `QA_ENABLE_RERANK`
- `QA_ENABLE_ANSWERABILITY_GATE`
- `QA_ENABLE_VERIFIER`
- `QA_ENABLE_VISION_VERIFY`
- `QA_QUERY_REWRITE_COUNT`

### `backend/schemas.py`

Extend response models with:

- `answerable`
- `confidence`
- `reason_codes`
- `pipeline_version`
- more debug trace fields for evals

## Definition of Done (under no-training constraint)

We can claim a strong "SOTA-for-this-product" upgrade when:

- benchmark exists and is versioned
- Recall@k and answer correctness improve materially vs current pipeline
- faithfulness improves (fewer unsupported claims)
- unanswerable handling is reliable
- p95 latency/cost remain acceptable
- all improvements are achieved without training/fine-tuning

