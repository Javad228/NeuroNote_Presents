# SlideParser Q&A RAG Pipeline

This document explains, in implementation-level detail, how Q&A works in this repository.

Scope:
- The runtime Q&A path from frontend question submission to rendered answer/highlights.
- Backend retrieval, reranking, answer generation, answerability, verification, and confidence.
- Index build/caching lifecycle.
- Streaming protocol and UI behavior.
- Evaluation script and benchmark format.

Primary code:
- `frontend-next/pages/lecture/[job_id].js`
- `backend/api/routes.py`
- `backend/services/question_answering.py`
- `backend/services/qa_index.py`
- `backend/services/openai_qa.py`
- `backend/schemas.py`
- `backend/config.py`
- `scripts/qa_eval.py`

---

## 1. End-to-End Runtime Flow (Default Pipeline: `v2`)

At runtime, the flow is:
1. User enters question in the lecture Q&A tab.
2. Frontend calls `POST /api/jobs/{job_id}/qa/answer/stream`.
3. Backend runs `QuestionAnsweringService.answer_question(...)`.
4. Service validates input, loads/builds QA index, retrieves candidates.
5. Service optionally rewrites query and reranks with LLM.
6. Service packs context units + region catalog.
7. Service optionally runs answerability gate.
8. If answerable: generate structured answer lines + highlights.
9. Service optionally verifies each answer line against cited context.
10. Service computes confidence and returns structured response.
11. Streaming route emits progress events, fake typing deltas, then final result.
12. Frontend renders answer cards and slide badges; clicking badge jumps/highlights slide regions.

---

## 2. Frontend Request and State Model

File: `frontend-next/pages/lecture/[job_id].js`

### 2.1 Turn state
The UI stores Q&A turns in `qaTurns`, each with:
- `id`
- `question`
- `status`: `loading | done | error`
- `result`: final response payload or `null`
- `error`: error string
- `progressMessage`: stage text while loading
- `streamText`: incremental typing text while loading

### 2.2 Submit behavior
`handleSubmitQa()` does:
1. Validate question is non-empty.
2. Abort existing in-flight request (`AbortController`) if any.
3. Increment request sequence (`qaRequestSeqRef`) and create new turn.
4. Call `streamQaAnswer(...)`.
5. Update turn from SSE callbacks:
   - `onProgress` updates `progressMessage`.
   - `onDelta` appends text to `streamText`.
   - `onResult` sets finalizing state.
6. On success: set turn `status=done`, attach `result`.
7. On error: set turn `status=error`.
8. Ignore stale responses if request sequence changed.

### 2.3 Streaming parser on frontend
`streamQaAnswer(...)` parses Server-Sent Events blocks:
- `event: progress` -> callback with parsed JSON
- `event: delta` -> callback with `{text}`
- `event: result` -> stores final payload and returns it
- `event: error` -> throws JS error
- Unknown events are ignored (including `done`)

If stream ends before a `result` event, frontend throws:
- `"Streaming ended before final result."`

### 2.4 Progress labels shown in UI
Mapped stage labels:
- `request_start` -> Preparing request...
- `index_ready` -> Loading lecture index...
- `query_rewrite_done` -> Rewriting query...
- `embedding_ready` -> Embedding question...
- `retrieval_ready` -> Retrieving relevant context...
- `rerank_done` -> Reranking context...
- `context_ready` -> Packing context...
- `answerability_done` -> Checking answerability...
- `answer_generating` -> Generating answer...
- `answer_ready` -> Answer generated.
- `verification_done` -> Verifying grounding...
- `request_done` -> Finalizing response...

---

## 3. API Endpoints and Contracts

File: `backend/api/routes.py`, schemas in `backend/schemas.py`

### 3.1 Request model
`QaAnswerRequest`:
- `question: str` (required, min 1, max 4000)
- `top_k: int | null` (1..50)
- `max_selected_units: int | null` (1..10)

### 3.2 Non-stream endpoint
`POST /api/jobs/{job_id}/qa/answer?debug=true|false`
- Calls service and returns validated `QaAnswerResponse`.
- Used by scripts like `scripts/qa_eval.py`.

### 3.3 Stream endpoint
`POST /api/jobs/{job_id}/qa/answer/stream?debug=true|false`
- Response media type: `text/event-stream`.
- Event types emitted:
  - `progress`
  - `delta`
  - `result`
  - `error`
  - `done`
- Keep-alive comments emitted periodically: `: keep-alive`

Streaming notes:
- The HTTP status is stream success if route is entered; pipeline errors are emitted as `event:error`.
- After worker completion, backend emits chunked `delta` tokens from `answer_text` (20ms delay each) for typing effect, then emits `result`.

### 3.4 Response model
`QaAnswerResponse`:
- `job_id`
- `question`
- `answer_text`
- `answer_lines[]` where each line has:
  - `line_index`
  - `text`
  - `highlights[]` with `slide_id`, `slide_number`, `region_id`
  - `unit_ids[]`
- `used_context`:
  - `selected_unit_ids[]`
  - `expanded_unit_ids[]`
  - `context_slide_ids[]`
- `timings_ms`:
  - `index_load_or_build`
  - `question_embedding`
  - `retrieval`
  - `llm_select`
  - `llm_answer`
  - `total`
- `answerable: bool`
- `confidence: float | null`
- `reason_codes[]`
- `pipeline_version: "v1" | "v2"`
- `debug` (optional structured trace)

---

## 4. Service Entry and Pipeline Dispatch

File: `backend/services/question_answering.py`

### 4.1 Input validation
`_validate_request_inputs(...)` enforces:
- `job_id` resolves to existing job dir else `404 Job not found`.
- `question.strip()` non-empty else `400 Question is required`.
- `top_k` in `[1,50]`.
- `max_selected_units` in `[1,10]`.

Defaults:
- `top_k = QA_DEFAULT_TOP_K` (default env value: `10`)
- `max_selected_units = QA_DEFAULT_MAX_SELECTED_UNITS` (default env value: `4`)

### 4.2 Pipeline selection
Pipeline selection is no longer configurable at runtime.
Q&A always executes the v2 pipeline.

---

## 5. QA Index Lifecycle

File: `backend/services/qa_index.py`

### 5.1 Data source for index
Index is derived from lecture payload (`LectureService.get_lecture_payload`):
- slides
- per-slide steps
- regions/clusters/groups metadata

Each step becomes one explanation "unit":
- `unit_id = "{chunk_id}:{slide_id}:{step_id}"`
- includes `explanation_text`, `slide_id`, `slide_number`, `step_number`, `region_ids`.

### 5.2 Text fields built per unit
- `unit_text_core`: step line only.
- `unit_text_script_aug`: line + script title + script summary + prev/next step lines.
- `unit_text_region_aux`: line + up to 3 region descriptions + up to 3 region kinds.

### 5.3 Index versions
- v1 payload file: `jobs/{job_id}/qa/explanation_index.v1.json`
- v2 payload file: `jobs/{job_id}/qa/explanation_index.v2.json`

### 5.4 Freshness and cache
Freshness is keyed by:
- index payload `version`
- `embedding_model`
- tokenizer version (`v1`)
- source `result.json` mtime + size fingerprint

In-memory cache key:
- `(job_id, qa_embed_model, index_version)`

Load order:
1. in-memory cache (if fresh)
2. disk index file (if fresh)
3. rebuild index and persist to disk

### 5.5 Dependencies
Lexical scoring uses `rank_bm25.BM25Okapi`.
If unavailable, QA indexing/ranking fails with `QaIndexUnavailableError`.

### 5.6 Tokenization and embedding normalization
- Tokenizer regex: `[A-Za-z0-9]+`, lowercased.
- Embeddings are L2-normalized before dot products.

---

## 6. Retrieval Algorithms

File: `backend/services/qa_index.py`

### 6.1 v1 retrieval (`rank_candidates`)
Inputs:
- single question embedding
- question text tokens

Scores:
- dense semantic score: `dot(normalized_q, normalized_unit_embedding)`
- lexical score: BM25 on `unit_tokens`

Rank fusion:
- Reciprocal Rank Fusion constant `k=60`
- `rrf = 1/(60 + semantic_rank) + 1/(60 + bm25_rank)`

Sort order:
1. descending `rrf_score`
2. descending semantic score
3. descending BM25 score
4. stable tie-break by `(slide_number, step_number, unit_id)`

Return top `top_k`.

### 6.2 v2 retrieval (`rank_candidates_v2`)
Inputs:
- query variants (`[original, rewrites...]`)
- embedding per variant

For each query variant, compute six score channels:
- `dense_core`
- `bm25_core`
- `dense_script_aug`
- `bm25_script_aug`
- `dense_region_aux`
- `bm25_region_aux`

Field weights:
- `dense_core: 1.00`
- `bm25_core: 0.95`
- `dense_script_aug: 0.55`
- `bm25_script_aug: 0.40`
- `dense_region_aux: 0.18`
- `bm25_region_aux: 0.15`

Per-query combined RRF score:
- `sum(field_weight / (60 + field_rank))`

Query-level weight:
- original query weight: `1.00`
- rewrite query weight: `0.70`

Final merged score:
- `sum(query_weight / (60 + per_query_rank))`

Additional metadata tracked:
- per-field raw scores for original query
- `query_hits`: variants where unit appears in that query's top `top_k_per_query`

Final sort:
1. descending merged `rrf_score`
2. descending `dense_core`
3. descending `bm25_core`
4. tie-break by `(slide_number, step_number, unit_id)`

Return up to `merged_cap`.

---

## 7. v2 Pipeline Stages in Detail

File: `backend/services/question_answering.py`

### 7.1 Stage: index load/build
- Load v2 index (`load_or_build_index(..., index_version="v2")`).
- Emits progress: `index_ready`.

### 7.2 Stage: query rewriting (optional)
Controlled by:
- `QA_ENABLE_QUERY_REWRITE` (default `true`)
- `QA_QUERY_REWRITE_COUNT` (default `2`)

LLM returns JSON `{"rewrites":[...]}`.
Question variants are deduped preserving order, and capped to `1 + rewrite_count`.
Emits progress: `query_rewrite_done`.

### 7.3 Stage: embedding
Embeds all query variants.
If returned vector count mismatches query count -> `502`.
Emits progress: `embedding_ready`.

### 7.4 Stage: retrieval
Runs `rank_candidates_v2(...)` with:
- `top_k_per_query = QA_RETRIEVE_TOP_K_PER_QUERY` (default `24`)
- `merged_cap = QA_MERGED_CANDIDATE_CAP` (default `30`)
Emits progress: `retrieval_ready`.

### 7.5 Stage: LLM rerank (optional)
Controlled by:
- `QA_ENABLE_LLM_RERANK` (default `true`)
- `QA_RERANK_CANDIDATES` (default `20`)
- `QA_RERANK_TOP_N` (default `8`)

Process:
1. Build rerank cards from top retrieval pool.
2. Ask LLM for `ranked_unit_ids`.
3. Validate IDs against provided candidate set.

If rerank disabled, top candidate IDs are used directly.
If no anchor IDs remain -> `502`.
Emits progress: `rerank_done`.

### 7.6 Stage: context packing
1. Diversity pack anchors (`_pack_anchor_units_diverse`):
   - first pass: one unit per slide in anchor order
   - second pass: fill remainder in anchor order
2. Expand neighbors (`_expand_context_units`):
   - include selected unit + previous/next unit on same slide
   - preserve global unit order
   - cap at `12` expanded units
3. Build region catalog (`_build_region_catalog_for_context`) using:
   - `only_referenced_regions=True`
   - `extra_visual_regions_per_slide=8`
   - `extra_text_regions_per_slide=2`
   - `max_total_regions=96`

Emits progress: `context_ready`.

### 7.7 Stage: answerability gate (optional)
Controlled by `QA_ENABLE_ANSWERABILITY_GATE` (default `true`).

LLM returns:
- `answerable: bool`
- `reason_code` in:
  - `sufficient_evidence`
  - `insufficient_retrieval_evidence`
  - `question_out_of_scope`
  - `ambiguous_question`

If gate says not answerable:
- returns abstention response immediately (`answerable=false`)
- adds reason code(s), including `abstained`
- confidence clamped to low value (typically `0.20`)

Emits progress: `answerability_done`.

### 7.8 Stage: answer generation
LLM is prompted to return strict JSON:
- `answer_lines[]`
- each line with `text`, `highlights[]`, `unit_ids[]`

Then `_validate_answer_lines(...)` sanitizes and grounds output.
Emits progress: `answer_generating`, then `answer_ready`.

### 7.9 Stage: verifier (optional)
Controlled by `QA_ENABLE_VERIFIER` (default `true`).

LLM returns per-line verdict:
- `supported`
- `partially_supported`
- `unsupported`

`_apply_verifier_results(...)` behavior:
- `unsupported` lines removed
- `partially_supported` lines optionally rewritten using `corrected_text`
- lines reindexed from 0

If all lines removed, pipeline returns abstention response.
Emits progress: `verification_done`.

### 7.10 Stage: confidence and reason codes
Confidence uses:
- verification ratio (weight 0.40)
- citation coverage (weight 0.30)
- retrieval margin (weight 0.20)
- answerable component (weight 0.10)

Where:
- citation coverage = cited lines / total lines
- retrieval margin uses top1 vs top3 merged candidate scores
- verification ratio defaults to `0.5` if verifier disabled

`low_confidence` reason code is added when confidence < 0.40.

### 7.11 Final response normalization
Before returning:
- timings are always populated
- `answerable`, `confidence`, `reason_codes`, `pipeline_version` are enforced
- reason codes deduped preserving order
- if `answerable=false`, confidence capped at `<=0.30`

---

## 8. v1 Pipeline (Legacy)

File: `backend/services/question_answering.py`

`v1` is simpler:
1. Load/build v1 index.
2. Embed single question.
3. Retrieve top candidates using v1 RRF.
4. LLM selects relevant unit IDs (`select_relevant_units`).
5. Expand neighbor units.
6. Build region catalog (all context-slide regions).
7. LLM generates answer lines + highlights.
8. Validate answer lines.
9. Return response.

Not present in v1:
- query rewrite
- v2 weighted multi-field retrieval
- answerability gate
- verifier stage
- confidence/reason-code logic

---

## 9. OpenAI Integration Details

File: `backend/services/openai_qa.py`

### 9.1 Transport
- Uses `httpx.AsyncClient`.
- API base defaults to `https://api.openai.com/v1`.
- Calls:
  - `/embeddings`
  - `/chat/completions`

### 9.2 Chat JSON helper behavior
`_chat_json_object(...)`:
1. Sends system prompt + JSON-serialized user payload.
2. Requests `response_format={"type":"json_object"}` when strict.
3. If API call fails and strict mode was used, retries without `response_format`.
4. Parses first `choices[0].message.content`.
5. Accepts raw string or list-like content.
6. Extracts JSON object (also handles fenced ```json blocks).
7. Requires final parsed payload to be JSON object.

### 9.3 LLM tasks and expected JSON
- `select_relevant_units` -> `selected_unit_ids`
- `rewrite_queries` -> `rewrites`
- `rerank_candidate_units` -> `ranked_unit_ids`
- `assess_answerability` -> `answerable`, `reason_code`
- `verify_answer_lines` -> `line_verdicts[]`
- `answer_with_highlights` -> `answer_lines[]`

All prompts instruct the model to prioritize `explanation_text` as primary evidence.

---

## 10. Answer-Line Validation and Grounding Rules

File: `backend/services/question_answering.py` (`_validate_answer_lines`)

For each raw answer line:
1. Keep only lines with non-empty `text`.
2. Keep only `unit_ids` that are in `context_units`, dedupe preserving order.
3. Keep only highlights whose `(slide_id, region_id)` exist in region catalog.
4. Compute allowed highlight keys from cited `unit_ids` and their `region_ids`.
5. If allowed set is non-empty, drop highlights outside allowed set.
6. If visual highlights exist, sort highlights to prefer visual first.
7. Keep line even if highlight list ends empty.

If no valid lines remain -> structured response error (`OpenAIJSONError`).

---

## 11. Highlight Rendering and Slide Jump UX

File: `frontend-next/pages/lecture/[job_id].js`

### 11.1 Slide references per line
`collectLineSlideRefs(line)`:
- collects unique `slide_id`s from line highlights
- prefers numeric slide number labels
- sorts by `slide_number`, then `slide_id`
- renders badges like `Slide 7`

### 11.2 Activating a line
`activateQaAnswerLine(lineIndex, turnId, slideId)`:
1. Set active turn, line, and target slide ID.
2. If slide exists and differs from current slide, call `goToSlide(...)`.

### 11.3 Region highlighting on active slide
When in Q&A tab and active line exists:
1. Filter line highlights to current slide.
2. Collect region IDs.
3. If visual IDs (`v:*`) exist, use only visual IDs.
4. Expand IDs through group/cluster relationships (`resolveExpandedIdsForSlide`).
5. Render overlay boxes/lifts/polygons for active regions.

Result:
- Clicking a slide badge moves to that slide and highlights referenced regions tied to that answer line.

---

## 12. Error Handling and Status Semantics

Common backend errors:
- `400`: bad request values (`question`, `top_k`, `max_selected_units`)
- `404`: job not found
- `409`: index or context unavailable (no units/candidates)
- `502`: provider request/JSON/structured response errors
- `504`: provider timeout
- `500`: unexpected server failure

Streaming-specific behavior:
- Worker exceptions are converted to SSE `error` event payloads.
- Stream still returns SSE response; frontend throws on `event:error`.

---

## 13. Debug Mode (`?debug=true`)

Debug payload may include:
- retrieval candidates (merged and/or base)
- query variants and raw rewrite output
- rerank input/output and raw rerank JSON
- region catalog summary
- answerability raw output
- verifier raw output and normalized verification results
- stage timings

This is especially useful for evaluating retrieval quality and pipeline decisions.

---

## 14. Configuration Knobs (Environment Variables)

File: `backend/config.py`

Core QA defaults:
- `QA_EMBED_MODEL=text-embedding-3-small`
- `QA_SELECT_MODEL=gpt-5-mini`
- `QA_ANSWER_MODEL=gpt-5`
- `QA_OPENAI_TIMEOUT_SECONDS=60`

v2 feature toggles:
- `QA_ENABLE_QUERY_REWRITE=true`
- `QA_ENABLE_LLM_RERANK=true`
- `QA_ENABLE_ANSWERABILITY_GATE=true`
- `QA_ENABLE_VERIFIER=true`

v2 sizing knobs:
- `QA_QUERY_REWRITE_COUNT=2`
- `QA_RETRIEVE_TOP_K_PER_QUERY=24`
- `QA_MERGED_CANDIDATE_CAP=30`
- `QA_RERANK_CANDIDATES=20`
- `QA_RERANK_TOP_N=8`
- `QA_DEFAULT_TOP_K=10`
- `QA_DEFAULT_MAX_SELECTED_UNITS=4`

Model overrides:
- `QA_REWRITE_MODEL` (falls back to `QA_SELECT_MODEL`)
- `QA_RERANK_MODEL` (falls back to `QA_SELECT_MODEL`)
- `QA_GATE_MODEL` (falls back to `QA_SELECT_MODEL`)
- `QA_VERIFY_MODEL` (falls back to `QA_SELECT_MODEL`)

---

## 15. Evaluation Pipeline

Files:
- `evals/qa_benchmark/README.md`
- `scripts/qa_eval.py`

### 15.1 Benchmark format (JSONL)
Required per row:
- `job_id`
- `question`
- `answerable`
- `gold_answer`
- `gold_unit_ids[]`

Optional:
- `gold_region_ids[]`
- `question_type`
- `notes`

### 15.2 Evaluator behavior
`qa_eval.py`:
1. Reads benchmark JSONL.
2. Calls non-stream endpoint `/api/jobs/{job_id}/qa/answer?debug=...`.
3. Records status, latency, pipeline version, predicted answerable, reason codes.
4. Computes retrieval recall from debug retrieval candidates vs `gold_unit_ids`.
5. Computes answerability confusion matrix and precision/recall/F1.
6. Writes JSON report with aggregate + per-example data.

---

## 16. Practical Notes and Invariants

1. The system is RAG over slide explanation units, not raw OCR chunks directly at query time.
2. `explanation_text` is consistently treated as primary evidence in prompts and ranking design.
3. Highlight IDs are constrained to known region catalog IDs and then further constrained by cited units.
4. The frontend ignores `done` SSE events; it relies on `result` to complete.
5. v2 can abstain twice:
   - after answerability gate
   - after verification removes all lines
6. Confidence is only meaningful in v2; v1 does not compute it.

---

## 17. Minimal Sequence Diagram

```text
User -> Frontend(QA tab): Ask question
Frontend -> Backend /qa/answer/stream: POST {question}
Backend -> QA Service: answer_question(...)
QA Service -> QA Index: load/build index
QA Service -> OpenAI: rewrite? + embeddings + rerank? + gate? + answer + verify?
QA Service -> Backend stream: progress events + final response
Backend stream -> Frontend: progress, delta, result
Frontend -> UI: render answer lines + slide badges
User -> UI: click slide badge
UI -> Lecture Viewer: jump to slide + highlight referenced regions
```
