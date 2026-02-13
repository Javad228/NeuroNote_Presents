# NeuroNote Presents Webapp

Web application to:

1. Upload a lecture PDF from a browser UI.
2. Render PDF pages to slide images.
3. Run changepoint chunking from `/home/javad/NeuroNote/chunking_slides_v2`.
4. Use Azure OCR via NeuroNote extraction modules (no Paddle OCR in this webapp flow).
5. Send each chunk to NeuroNote API at `/home/javad/NeuroNote_Pipeline/neuronote`:
- 1 slide chunk -> `/api/process?sync=true`
- 2+ slide chunk -> `/api/process-batch?sync=true` with only `images` fields

## Project Structure

- `backend/main.py`: FastAPI app wiring + static frontend serving
- `backend/api/routes.py`: HTTP endpoints
- `backend/config.py`: environment configuration
- `backend/schemas.py`: option model(s)
- `backend/services/pdf_render.py`: PDF -> PNG page rendering
- `backend/services/chunking.py`: changepoint chunking integration
- `backend/services/neuronote_client.py`: upstream NeuroNote API client
- `backend/services/orchestrator.py`: end-to-end orchestration flow
- `backend/services/jobs.py`: local jobs metadata + thumbnail resolution
- `frontend/index.html`: dashboard UI (sidebar/topbar/jobs grid)
- `frontend/style.css`: dashboard styles
- `frontend/app.js`: jobs fetch + client-side search
- `app.py`: compatibility entrypoint (`uvicorn app:app`)

## Requirements

Install local webapp deps:

```bash
cd /home/javad/NeuroNote_Presents
pip install -r requirements.txt
```

Also ensure external dependencies are installed for:

- chunking repo: `/home/javad/NeuroNote/chunking_slides_v2`
- NeuroNote server: `/home/javad/NeuroNote_Pipeline/neuronote`

## Environment Variables

- `CHUNKING_ROOT` (default: `/home/javad/NeuroNote/chunking_slides_v2`)
- `NEURONOTE_PIPELINE_ROOT` (default: `/home/javad/NeuroNote_Pipeline`)
- `NEURONOTE_API_BASE` (default: `http://127.0.0.1:8000`)
- `JOBS_ROOT` (default: `/home/javad/NeuroNote_Presents/jobs`)
- `RENDER_DPI` (default: `200`)
- `NEURONOTE_TIMEOUT_SECONDS` (default: `3600`)
- `MAX_PDF_SIZE_MB` (default: `100`)
- `AZURE_OCR_LEVEL` (default: `lines`)
- `AZURE_OCR_MIN_CONF` (default: `0.0`)

Azure OCR credentials (required):

- `AZURE_DOC_INTEL_ENDPOINT`
- `AZURE_DOC_INTEL_KEY`

## Run

Start NeuroNote API first (default expected at `http://127.0.0.1:8000`), then run this app:

```bash
cd /home/javad/NeuroNote_Presents
uvicorn backend.main:app --host 0.0.0.0 --port 8100 --reload
```

Compatibility run command:

```bash
uvicorn app:app --host 0.0.0.0 --port 8100 --reload
```

Open:

- UI: `http://127.0.0.1:8100/`
- Health: `http://127.0.0.1:8100/healthz`
- API docs: `http://127.0.0.1:8100/docs`

## API

`GET /api/jobs` (filesystem-backed jobs listing)

`GET /api/jobs/{job_id}/thumbnail` (job thumbnail image)

`POST /api/process-pdf` (multipart form with `pdf` file)

Query params:

- `method`: `pelt|window|binseg`
- `penalty`: float (optional)
- `n_bkps`: int (optional)
- `min_chunk`: int
- `use_embeddings`: bool
- `use_cache`: bool
- `skip_generation`: bool
- `previous_context`: string (forwarded only to `/api/process` single-slide calls)
- `render_dpi`: int
