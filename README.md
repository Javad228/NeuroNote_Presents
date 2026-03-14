# SlideParser Webapp

Web application to:

1. Upload a lecture PDF from a browser UI.
2. Render PDF pages to slide images.
3. Upload rendered slide images to GCS.
4. Submit image object paths to upstream `/api/process-batch-gcs`.
5. Poll upstream job status and store results in local `jobs/<job_id>/result.json`.

## Project Structure

- `backend/main.py`: FastAPI app wiring + API/static legacy routes
- `backend/api/routes.py`: HTTP endpoints
- `backend/config.py`: environment configuration
- `backend/schemas.py`: option model(s)
- `backend/services/pdf_render.py`: PDF -> PNG page rendering
- `backend/services/chunking.py`: legacy local chunking integration (not used by `/api/process-pdf`)
- `backend/services/neuronote_client.py`: upstream SlideParser API client
- `backend/services/orchestrator.py`: end-to-end orchestration flow
- `backend/services/transcript_audio.py`: wrapper that calls `transcript_to_audio.py` after job completion
- `backend/services/jobs.py`: local jobs metadata + thumbnail resolution
- `frontend-next/`: Next.js frontend (primary UI)
  - `frontend-next/pages/index.js`: dashboard route
  - `frontend-next/pages/new-project.js`: upload/project route
  - `frontend-next/pages/lecture/[job_id].js`: lecture player route
  - `frontend-next/public/static/*`: migrated CSS/JS assets from legacy frontend
- `frontend/`: legacy static frontend source (kept for reference)
- `app.py`: compatibility entrypoint (`uvicorn app:app`)

## Requirements

Install local webapp deps:

```bash
cd /home/javad/NeuroNote_Presents
pip install -r requirements.txt
```

Install Next.js frontend deps:

```bash
cd /home/javad/NeuroNote_Presents/frontend-next
npm install
```

Also ensure external dependencies are installed for:

- upstream SlideParser server (configured by `NEURONOTE_API_BASE`)

## Environment Variables

- `CHUNKING_ROOT` (default: `/home/javad/NeuroNote/chunking_slides_v2`, compatibility-only in current upstream flow)
- `NEURONOTE_PIPELINE_ROOT` (default: `/home/javad/NeuroNote_Pipeline`)
- `NEURONOTE_ARTIFACT_ROOTS` (optional: extra roots to resolve `/output/...` artifacts, comma or `:` separated)
- `NEURONOTE_API_BASE` (default: `http://127.0.0.1:8000`)
- `JOBS_ROOT` (default: `/home/javad/NeuroNote_Presents/jobs`)
- `CORS_ALLOW_ORIGINS` (default: `http://localhost:3000,http://127.0.0.1:3000`)
- `RENDER_DPI` (default: `200`)
- `NEURONOTE_TIMEOUT_SECONDS` (default: `3600`)
- `NEURONOTE_POLL_INTERVAL_SECONDS` (default: `2.0`)
- `MAX_PDF_SIZE_MB` (default: `100`)
- `AZURE_OCR_LEVEL` (default: `lines`, compatibility-only in current upstream flow)
- `AZURE_OCR_MIN_CONF` (default: `0.0`, compatibility-only in current upstream flow)
- `AZURE_SDK_LOG_LEVEL` (default: `WARNING`; set `INFO` to re-enable Azure HTTP logs)
- `GCS_IMAGES_BUCKET` (default: `lectura-images`)
- `GCS_IMAGES_PREFIX` (default: empty)
- `TRANSCRIPT_TTS_ENABLED` (default: `true`; auto-generate `transcript_audio.wav` after upstream job completes)
- `TRANSCRIPT_TTS_PROVIDER` (default: `elevenlabs`; options: `elevenlabs`, `openai`)
- `TRANSCRIPT_TTS_MODEL` (default depends on provider: `elevenlabs=eleven_flash_v2_5`, `openai=gpt-4o-mini-tts`)
- `TRANSCRIPT_TTS_VOICE` (default depends on provider: `elevenlabs=Matilda`, `openai=marin`)
- `TRANSCRIPT_TTS_ELEVENLABS_OUTPUT_FORMAT` (default: `pcm_24000`; use `wav_*` or `pcm_*`)
- `TRANSCRIPT_TTS_MAX_CHARS` (default: `3500`)
- `TRANSCRIPT_TTS_INSTRUCTIONS` (default: patient university instructor style)
- `TRANSCRIPT_TTS_NO_SLIDE_HEADINGS` (default: `false`)
- `TRANSCRIPT_TTS_VERBOSE` (default: `false`)
- `TRANSCRIPT_TTS_FAIL_ON_ERROR` (default: `false`; set `true` to fail `/api/process-pdf` when TTS fails)

Azure OCR credentials (required):

- `AZURE_DOC_INTEL_ENDPOINT`
- `AZURE_DOC_INTEL_KEY`

Transcript audio credentials (required for transcript audio generation):

- `ELEVENLABS_API_KEY` (when `TRANSCRIPT_TTS_PROVIDER=elevenlabs`, default)
- `OPENAI_API_KEY` (when `TRANSCRIPT_TTS_PROVIDER=openai`)

## Run

Start the upstream SlideParser API first (default expected at `http://127.0.0.1:8000`), then run backend:

```bash
cd /home/javad/NeuroNote_Presents
uvicorn backend.main:app --host 0.0.0.0 --port 8100 --reload
```

Then run Next.js frontend:

```bash
cd /home/javad/NeuroNote_Presents/frontend-next
npm run dev
```

Optional frontend env (recommended for long-running requests in local dev):

- `NEXT_PUBLIC_BACKEND_ORIGIN` (example: `http://127.0.0.1:8100`)

Compatibility backend run command:

```bash
uvicorn app:app --host 0.0.0.0 --port 8100 --reload
```

Open:

- UI (Next.js): `http://127.0.0.1:3000/`
- Health: `http://127.0.0.1:8100/healthz`
- API docs: `http://127.0.0.1:8100/docs`

Notes:

- Next.js proxies `/api/*` and `/healthz` to backend via `frontend-next/next.config.js`.
- You can override backend origin by setting `BACKEND_ORIGIN` before `npm run dev` (default: `http://127.0.0.1:8100`).
- New Project submit script can call backend directly via `NEXT_PUBLIC_BACKEND_ORIGIN` (avoids proxy timeout risk for long-running `/api/process-pdf` requests).
- On each `POST /api/process-pdf`, rendered slide images are uploaded to
  `gs://$GCS_IMAGES_BUCKET/$GCS_IMAGES_PREFIX/<job_id>/` (prefix omitted when empty).
- This backend does not run local chunking/OCR during `/api/process-pdf`; it only uploads images and delegates processing upstream.
- After upload, the backend submits one `/api/process-batch-gcs` request using `object_paths` (without `chunks`) and polls
  `/api/jobs/{job_id}` until status is `complete`.
- After upstream completion, the backend generates transcript audio (`transcript_audio.wav`) and per-step timing metadata (`transcript_audio_timestamps.json`) via `transcript_to_audio.py`.
- GCS authentication uses Application Default Credentials (`GOOGLE_APPLICATION_CREDENTIALS` or `gcloud auth application-default login`).

## API

`GET /api/jobs` (filesystem-backed jobs listing)

`GET /api/jobs/{job_id}/thumbnail` (job thumbnail image)

`POST /api/process-pdf` (multipart form with `pdf` file)

Query params:

- `method`: `pelt|window|binseg` (compatibility-only; ignored in current upstream flow)
- `penalty`: float (optional, compatibility-only; ignored in current upstream flow)
- `n_bkps`: int (optional, compatibility-only; ignored in current upstream flow)
- `min_chunk`: int (compatibility-only; ignored in current upstream flow)
- `use_embeddings`: bool (compatibility-only; ignored in current upstream flow)
- `use_cache`: bool (compatibility-only; ignored in current upstream flow)
- `skip_generation`: bool
- `previous_context`: string (compatibility-only; ignored in current upstream flow)
- `render_dpi`: int
