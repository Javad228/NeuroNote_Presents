import asyncio
import contextlib
import json
import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse

from ..config import AppConfig, get_config
from ..services.lecture import LectureService
from ..services.jobs import JobsService
from ..schemas import ProcessPdfOptions, QaAnswerRequest, QaAnswerResponse
from ..services.orchestrator import OrchestratorService
from ..services.question_answering import QuestionAnsweringService


router = APIRouter()
logger = logging.getLogger(__name__)


def _sse_event(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/jobs")
async def list_jobs(
    config: AppConfig = Depends(get_config),
) -> dict[str, list[dict[str, Any]]]:
    service = JobsService(config)
    return {"jobs": service.list_jobs()}


@router.get("/api/jobs/{job_id}/thumbnail")
async def get_job_thumbnail(
    job_id: str,
    config: AppConfig = Depends(get_config),
) -> FileResponse:
    service = JobsService(config)
    thumbnail_path = service.get_thumbnail_path(job_id)

    if thumbnail_path is None:
        raise HTTPException(status_code=404, detail="Thumbnail not found.")
    if not thumbnail_path.exists() or not thumbnail_path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found.")

    return FileResponse(path=thumbnail_path)


@router.get("/api/jobs/{job_id}/lecture")
async def get_job_lecture(
    job_id: str,
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    service = LectureService(config)
    lecture = service.get_lecture_payload(job_id)
    if lecture is None:
        raise HTTPException(status_code=404, detail="Job lecture not found.")
    return lecture


@router.get("/api/jobs/{job_id}/slides/{slide_name}")
async def get_job_slide_image(
    job_id: str,
    slide_name: str,
    config: AppConfig = Depends(get_config),
) -> FileResponse:
    service = LectureService(config)
    slide_path = service.get_slide_image_path(job_id, slide_name)
    if slide_path is None:
        raise HTTPException(status_code=404, detail="Slide image not found.")
    return FileResponse(path=slide_path)


@router.get("/api/jobs/{job_id}/slides/{slide_name}/rendered")
async def get_job_slide_rendered(
    job_id: str,
    slide_name: str,
    step_index: int = Query(0, ge=0),
    strength: float = Query(1.0, ge=0.0, le=2.0),
    config: AppConfig = Depends(get_config),
) -> Response:
    service = LectureService(config)
    data = service.render_step_text_recolor_image(
        job_id=job_id,
        slide_name=slide_name,
        step_index=step_index,
        strength=strength,
    )
    if data is None:
        raise HTTPException(status_code=404, detail="Rendered slide not found.")
    return Response(content=data, media_type="image/jpeg")


@router.get("/api/jobs/{job_id}/rendered/{image_name}/{filename}")
async def get_job_precomputed_rendered_image(
    job_id: str,
    image_name: str,
    filename: str,
    config: AppConfig = Depends(get_config),
) -> FileResponse:
    service = LectureService(config)
    rendered_path = service.get_precomputed_rendered_image_path(job_id, image_name, filename)
    if rendered_path is None:
        raise HTTPException(status_code=404, detail="Rendered image not found.")
    return FileResponse(path=rendered_path, media_type="image/jpeg")


@router.get("/api/jobs/{job_id}/input-pdf")
async def get_job_input_pdf(
    job_id: str,
    config: AppConfig = Depends(get_config),
) -> FileResponse:
    service = LectureService(config)
    pdf_path = service.get_input_pdf_path(job_id)
    if pdf_path is None:
        raise HTTPException(status_code=404, detail="Input PDF not found.")
    return FileResponse(path=pdf_path, filename=pdf_path.name, media_type="application/pdf")


@router.get("/api/jobs/{job_id}/audio")
async def get_job_audio(
    job_id: str,
    config: AppConfig = Depends(get_config),
) -> FileResponse:
    service = LectureService(config)
    audio_path = service.get_transcript_audio_path(job_id)
    if audio_path is None:
        raise HTTPException(status_code=404, detail="Audio not found.")

    media_type = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
    }.get(audio_path.suffix.lower(), "application/octet-stream")

    return FileResponse(path=audio_path, filename=audio_path.name, media_type=media_type)


@router.post("/api/jobs/{job_id}/qa/answer", response_model=QaAnswerResponse)
async def answer_job_question(
    job_id: str,
    payload: QaAnswerRequest,
    debug: bool = Query(False),
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    service = QuestionAnsweringService(config)
    try:
        result = await service.answer_question(job_id=job_id, request=payload, debug=debug)
        return QaAnswerResponse.model_validate(result).model_dump()
    except HTTPException:
        raise
    except Exception:
        logger.exception("qa.answer_route_failed job_id=%s", job_id)
        raise HTTPException(status_code=500, detail="QA response failed.") from None


@router.post("/api/jobs/{job_id}/qa/answer/stream")
async def answer_job_question_stream(
    job_id: str,
    payload: QaAnswerRequest,
    request: Request,
    debug: bool = Query(False),
    config: AppConfig = Depends(get_config),
) -> StreamingResponse:
    service = QuestionAnsweringService(config)

    async def stream_events() -> Any:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        result_holder: dict[str, Any] = {}
        error_holder: dict[str, Any] = {}

        async def progress_cb(event_payload: dict[str, Any]) -> None:
            await queue.put({"event": "progress", "payload": event_payload})

        async def run_answer() -> None:
            try:
                result = await service.answer_question(
                    job_id=job_id,
                    request=payload,
                    debug=debug,
                    progress_cb=progress_cb,
                )
                result_holder["result"] = QaAnswerResponse.model_validate(result).model_dump()
            except HTTPException as exc:
                error_holder["error"] = {"status_code": int(exc.status_code), "detail": str(exc.detail)}
            except Exception:
                logger.exception("qa.answer_stream_route_failed job_id=%s", job_id)
                error_holder["error"] = {"status_code": 500, "detail": "QA response failed."}

        worker = asyncio.create_task(run_answer())
        try:
            while True:
                if await request.is_disconnected():
                    worker.cancel()
                    break

                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.35)
                    if isinstance(item, dict) and isinstance(item.get("event"), str) and isinstance(item.get("payload"), dict):
                        yield _sse_event(str(item["event"]), item["payload"])
                    continue
                except asyncio.TimeoutError:
                    pass

                if worker.done():
                    break
                yield ": keep-alive\n\n"

            while not queue.empty():
                item = queue.get_nowait()
                if isinstance(item, dict) and isinstance(item.get("event"), str) and isinstance(item.get("payload"), dict):
                    yield _sse_event(str(item["event"]), item["payload"])

            if "error" in error_holder and isinstance(error_holder["error"], dict):
                yield _sse_event("error", error_holder["error"])
                yield _sse_event("done", {"ok": False})
                return

            result = result_holder.get("result")
            if isinstance(result, dict):
                answer_text = result.get("answer_text")
                if isinstance(answer_text, str) and answer_text.strip():
                    # Emit answer in small word-like chunks with a short delay so the UI
                    # can render visible incremental typing instead of a single burst.
                    chunks = re.findall(r"\S+\s*|\n+", answer_text)
                    if not chunks:
                        chunks = [answer_text]
                    for chunk in chunks:
                        if not chunk:
                            continue
                        yield _sse_event("delta", {"text": chunk})
                        await asyncio.sleep(0.02)
                yield _sse_event("result", result)
                yield _sse_event("done", {"ok": True})
                return

            yield _sse_event("error", {"status_code": 500, "detail": "No result was produced."})
            yield _sse_event("done", {"ok": False})
        finally:
            if not worker.done():
                worker.cancel()
            with contextlib.suppress(Exception):
                await worker

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/process-pdf")
async def process_pdf(
    pdf: UploadFile = File(...),
    method: str = Query("pelt", pattern="^(pelt|window|binseg)$"),
    penalty: Optional[float] = Query(None, description="Compatibility-only; ignored in current upstream flow."),
    n_bkps: Optional[int] = Query(
        None,
        ge=1,
        description="Compatibility-only; ignored in current upstream flow.",
    ),
    min_chunk: int = Query(2, ge=1),
    use_embeddings: bool = Query(True),
    use_cache: bool = Query(True),
    skip_generation: bool = Query(False, description="Forwarded to NeuroNote API."),
    previous_context: Optional[str] = Query(
        None,
        description="Compatibility-only; ignored in current upstream flow.",
    ),
    render_dpi: Optional[int] = Query(None, ge=72, le=600),
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    filename = (pdf.filename or "").lower()
    if not filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported.")

    pdf_bytes = await pdf.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty.")
    max_pdf_bytes = config.max_pdf_size_mb * 1024 * 1024
    if len(pdf_bytes) > max_pdf_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"PDF exceeds size limit of {config.max_pdf_size_mb} MB.",
        )

    options = ProcessPdfOptions(
        method=method,
        penalty=penalty,
        n_bkps=n_bkps,
        min_chunk=min_chunk,
        use_embeddings=use_embeddings,
        use_cache=use_cache,
        skip_generation=skip_generation,
        previous_context=previous_context,
        render_dpi=render_dpi or config.default_render_dpi,
    )

    orchestrator = OrchestratorService(config)

    try:
        return await orchestrator.process_pdf(
            pdf_bytes=pdf_bytes,
            options=options,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
