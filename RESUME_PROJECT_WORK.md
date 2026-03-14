# NeuroNote / SlideParser - Detailed Project Contribution Record

Author attribution in git: **Javad Baghirov** (`Javad228@users.noreply.github.com`)  
Project path: `/home/javad/NeuroNote_Presents`  
Document generated: 2026-03-04

---

## 1) What You Built (High-Level)

You built an end-to-end lecture processing and Q&A application that:

- Accepts PDF lecture uploads from a web UI.
- Renders each page into slide images.
- Uploads images to Google Cloud Storage.
- Submits batch jobs to an upstream SlideParser/NeuroNote service.
- Polls for completion and stores local job artifacts.
- Reconstructs lecture metadata (slides, steps, regions, highlights).
- Generates transcript narration audio (OpenAI/ElevenLabs).
- Serves a lecture player with visual step highlights and synchronized playback.
- Adds a retrieval-augmented Q&A pipeline with grounding, verification, and confidence scoring.

This is a full-stack system across backend orchestration, frontend product UX, model/tool integration, and evaluation tooling.

---

## 2) Chronological Build Timeline (From Git Commits)

### 2026-02-12 - `dec2e27` - **Presentation Pipeline**

You created the initial production skeleton of the project:

- Bootstrapped FastAPI backend app structure (`backend/*`).
- Implemented PDF rendering pipeline (`pdf_render.py`, PyMuPDF-based rendering).
- Built orchestration flow for processing uploaded PDFs (`orchestrator.py`).
- Added job metadata and artifact management (`jobs.py`).
- Implemented lecture payload assembly and per-slide step reconstruction (`lecture.py`).
- Added OCR/chunking bridge integration to external chunking repo and Azure OCR backend override (`chunking.py`, `chunking_ocr_bridge.py`).
- Built HTTP client for upstream processing APIs (`neuronote_client.py`).
- Implemented text recoloring utility for highlighted text overlays (`text_recolor.py`).
- Added transcript-to-audio generation script (`transcript_to_audio.py`).
- Built first frontend experience in static HTML/CSS/JS (`frontend/*`) for dashboard, upload flow, and lecture playback.
- Added core API routes, env config, schema models, README, and requirements.

Net effect: You shipped the first fully working pipeline version in one large foundation commit.

### 2026-02-16 - `8fcb209` - **Converted to Next.js**

You migrated the frontend architecture from static pages to Next.js:

- Created `frontend-next` app.
- Added Next routes for dashboard, lecture, and new project pages.
- Migrated legacy static JS/CSS assets into `frontend-next/public/static`.
- Added Next proxy rewrites to backend APIs (`next.config.js`).
- Updated project wiring and docs for dual backend/frontend dev workflow.

Net effect: You moved from static frontend delivery to a modern React/Next app foundation.

### 2026-02-17 - `003798f` - **RunPod + Audio Stabilization**

You expanded pipeline integrations and runtime stability:

- Added GCS upload service (`gcs_upload.py`) for slide image upload and URI/object tracking.
- Added transcript audio service wrapper (`transcript_audio.py`) integrated into backend orchestration.
- Updated orchestrator for upstream GCS batch processing and robust job polling.
- Extended backend configuration for cloud/API controls.
- Added RunPod sliding-window inference client utility (`runpod_sliding_window.py`).
- Generated chunking/OCR code bundle documentation (`docs/chunking_ocr_code_bundle.md`).
- Updated frontend flows to support improved pipeline behavior.

Net effect: You integrated external compute/storage services and made the end-to-end path more stable.

### 2026-02-22 - `4b62f69` - **Polygon Highlighting Upgrade**

You improved lecture highlight visualization:

- Enhanced polygon overlay behavior and styling.
- Improved lecture highlight rendering interactions in lecture page logic/CSS.

Net effect: Better visual grounding and readability for step-level highlights.

### 2026-02-23 - `a68297c` - **Removed Cluster-Level BBoxes**

You simplified highlight semantics:

- Removed cluster-level bounding box behavior from lecture highlight UI.
- Refined region-level visual behavior to reduce noisy overlay artifacts.

Net effect: Cleaner and more interpretable highlight UX.

### 2026-02-25 - `00e59f8` - **UI Redesign**

You delivered a major frontend redesign:

- Refreshed dashboard, upload page, and lecture views.
- Reworked lecture/player styling and interaction layout.
- Updated static CSS/JS logic significantly for better UX and visual consistency.

Net effect: A substantially more polished product-facing UI.

### 2026-02-28 - `8759df2` - **Q&A RAG Pipeline + Docs + Plan**

You implemented the largest intelligence upgrade in the repo:

- Added structured QA backend modules:
- `openai_qa.py` for model calls (embeddings + chat JSON workflows).
- `qa_index.py` for index build/load/cache and retrieval ranking.
- `question_answering.py` for full v2 QA pipeline orchestration.
- Added API contracts for QA request/response in `schemas.py`.
- Added QA answer route and streaming answer route in `routes.py`.
- Added benchmark scaffolding (`evals/qa_benchmark/*`).
- Added evaluation runner (`scripts/qa_eval.py`) with latency/recall/answerability metrics.
- Added deep implementation docs (`notes/RAG_PIPELINE.md`).
- Added forward implementation plan (`plan.md`).
- Reworked lecture frontend into richer Q&A-capable experience with streaming responses and click-to-highlight grounding.

Net effect: You converted the project from pipeline-only playback into a grounded, retrieval-driven question-answering product.

---

## 3) Detailed Technical Work You Implemented

## Backend Platform and API Surface

You built and maintained a FastAPI backend with:

- Health and job listing endpoints.
- Job thumbnail/image/audio/PDF serving routes.
- PDF processing endpoint with validation and option handling.
- Lecture payload endpoint for reconstructed slide-step metadata.
- QA answer endpoint and SSE streaming endpoint.
- CORS configuration with local loopback compatibility.
- Startup lifecycle management for artifact directories.
- Compatibility app entrypoint preserving `uvicorn app:app` workflow.

## Orchestration and Data Flow

You implemented end-to-end orchestration logic that:

- Creates unique job IDs and job directories.
- Persists uploaded PDFs and rendered slide images.
- Uploads slide images to GCS and tracks uploaded objects.
- Submits upstream batch jobs and polls status until terminal state.
- Handles upstream failure states with actionable errors.
- Converts upstream status payloads into normalized local chunk metadata.
- Writes full `result.json` artifacts locally for each job.
- Triggers transcript audio generation post-processing.
- Stores transcript generation status and metadata in job results.

## Lecture Reconstruction and Visual Grounding

You created a lecture service that:

- Reads upstream script/regions/debug artifacts and maps them to local slides.
- Builds slide-step metadata including `step_id`, line text, dwell timing, and region IDs.
- Merges visual-plan annotations (`what`, `description_of_how_it_looks`) where available.
- Resolves region/cluster/group structures for overlay highlighting.
- Generates per-step rendered images with text recoloring.
- Serves both on-demand rendered images and precomputed step images.
- Injects transcript timing data (`audio_start_ms`/`audio_end_ms`) from timestamp artifacts.

## Text Highlight Rendering Quality

You implemented image-processing logic for readable text emphasis:

- Contrast-aware recoloring for dark/light backgrounds.
- Edge-gated masks to avoid tinting whole highlight boxes.
- Glyph-constrained glow/stroke effects for legibility.
- Region-limited recolor application on active step IDs.

## QA / RAG System (v2 Pipeline)

You implemented a full retrieval + generation + verification stack:

- Built per-job QA indexes with disk and in-memory freshness caching.
- Added source fingerprinting based on `result.json` metadata.
- Constructed explanation units from lecture step data and region metadata.
- Built multi-view retrieval fields:
- core explanation text
- script-augmented text
- visual-step text
- region auxiliary text
- Implemented hybrid dense + BM25 retrieval with weighted RRF fusion.
- Added query rewrite expansion (optional, configurable).
- Added LLM reranking stage for candidate refinement.
- Added diversity-aware context packing and neighbor expansion.
- Added answerability gating before final answer generation.
- Added structured answer generation with strict unit/highlight grounding.
- Added post-answer verification stage with support/partial/unsupported verdicts.
- Added fallback answer logic when verifier removes content.
- Added confidence scoring based on retrieval/citation/verification signals.
- Added reason-code diagnostics (`low_confidence`, `partial_verification`, etc.).

## OpenAI Integration Layer

You implemented robust model-client utilities:

- Embedding API batching.
- Chat-completion JSON extraction/parsing.
- Structured helper methods for:
- query rewriting
- reranking
- answerability assessment
- answer generation with highlights
- line-by-line verification
- Resilient error normalization for timeout, transport, and invalid JSON cases.

## Frontend Product Work

You delivered substantial UX across three major surfaces:

### Dashboard (`/`)

- Job library listing with status/time/page/chunk metadata.
- Search/filter behavior over job fields.
- Keyboard-accessible job card navigation.
- Theme switch support (light/dark) with persisted preference.
- Redesigned sidebar, hero section, and card-based visual system.

### New Project (`/new-project`)

- File validation for PDF type and size limits.
- Drag-and-drop upload experience.
- Submission status/error handling and redirect flow.
- Narration preset selection with provider/model/voice mapping.
- Per-request pipeline query options for processing.

### Lecture Player (`/lecture/[job_id]`)

- Slide + step navigation and state management.
- Playback timeline generation from dwell or real audio timestamps.
- Audio synchronization between UI step index and media time.
- Scrubbing controls and playback-rate behavior.
- Rich animated overlays for text/visual highlights.
- QA tab with multi-turn chat and streamed answer rendering.
- Clickable answer-line slide badges that jump and activate highlights.
- SSE event parsing (`progress`, `delta`, `result`, `error`, `done`).
- Request cancellation, stale-response protection, auto-scroll behavior.

## Frontend System Modernization (Current Work in Progress)

Your current uncommitted work shows a major system-level frontend modernization:

- Migrated away from legacy per-page CSS files to global + component styling.
- Introduced Tailwind CSS configuration and tokenized theme variables.
- Added shadcn/Radix component primitives (`button`, `card`, `tabs`, `scroll-area`, etc.).
- Added reusable `cn` utility and alias-based import structure.
- Expanded lecture-page animation and highlight effect systems.
- Continued layout and interaction refinements across dashboard/new project/lecture pages.

## Audio Generation and TTS Tooling

You built and expanded transcript narration generation:

- Job transcript extraction from step artifacts.
- Text chunking for provider request size limits.
- Multi-chunk WAV merging.
- Per-step audio timestamp tracking and JSON output.
- Provider support logic for OpenAI and ElevenLabs.
- Voice/model/provider defaults and overrides.
- Runtime integration of generated audio into lecture payload/API.

## Evaluation and QA Benchmarking Tooling

You implemented first-pass QA evaluation infrastructure:

- JSONL benchmark schema definition.
- CLI evaluator hitting backend QA endpoint.
- Metrics collection:
- latency percentiles
- retrieval recall
- answerability precision/recall/F1
- reason-code distribution
- per-example reporting
- Structured report output for iterative QA pipeline improvements.

## Documentation and Engineering Planning

You authored extensive documentation:

- Full project runtime setup and environment variable docs (`README.md`).
- Deep QA pipeline technical walkthrough (`notes/RAG_PIPELINE.md`).
- Detailed implementable roadmap for QA improvements (`plan.md`).
- Chunking/OCR code bundle documentation for integration/debugging.

---

## 4) Technical Stack You Used

- **Backend:** Python, FastAPI, httpx, Pydantic/dataclasses.
- **Frontend:** Next.js 14, React 18, JavaScript.
- **Styling/UI:** Tailwind CSS, Radix UI, shadcn patterns, custom CSS animation systems.
- **Cloud/Storage:** Google Cloud Storage integration.
- **AI/LLM:** OpenAI embeddings/chat APIs; optional ElevenLabs TTS provider.
- **Retrieval:** BM25 (`rank-bm25`) + dense embeddings + weighted RRF fusion.
- **PDF/Image:** PyMuPDF, OpenCV.
- **Dev tooling:** custom CLI scripts for QA eval and visual capture automation.

---

## 5) Ownership Evidence (From Git)

Repository evidence indicates:

- **7 commits** authored by you in this repo history.
- **109 file-touch events** across your authored commits.
- Approximate line delta from your commits: **+27,252 / -5,176** (via `git log --numstat`).
- Current local WIP (not yet committed): large redesign and integration updates across backend, frontend, and audio tooling.

---

## 6) Resume-Ready Achievement Bullets

Use/adapt these directly in your resume:

- Built a full-stack lecture-processing platform that ingests PDFs, renders slides, orchestrates cloud batch processing, and serves reconstructed lecture playback artifacts.
- Designed and implemented a FastAPI orchestration backend with job lifecycle management, artifact persistence, upstream API polling, GCS integration, and audio post-processing.
- Developed a grounded Q&A RAG system (retrieval, reranking, answerability gating, verification, confidence scoring) over slide explanation units and region metadata.
- Implemented SSE-based streaming Q&A responses with progress stages and incremental typing UX in a Next.js lecture interface.
- Shipped multi-provider transcript narration pipeline (OpenAI/ElevenLabs) with text chunking, WAV merging, and per-step timestamp alignment.
- Migrated frontend architecture from static HTML/JS to Next.js and led multiple UI redesign iterations across dashboard, upload, and lecture player experiences.
- Created QA benchmarking/evaluation tooling with latency, retrieval, and answerability metrics to support evidence-driven iteration.
- Authored deep technical documentation and implementation plans for pipeline architecture and retrieval QA improvements.

---

## 7) Notes on Scope

This write-up is based on repository code and commit history in this workspace.  
If you want, this can be split into:

- a one-page recruiter-friendly summary,
- a technical portfolio deep-dive version,
- and role-targeted variants (ML Engineer, Full-Stack Engineer, Applied AI Engineer).

