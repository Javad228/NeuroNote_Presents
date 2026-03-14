import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response

from ..config import AppConfig, get_config
from ..services.lecture_export import LectureExportService
from ..services.lecture import LectureService
from ..services.jobs import JobsService
from ..schemas import ProcessPdfOptions
from ..services.orchestrator import OrchestratorService


router = APIRouter()
logger = logging.getLogger(__name__)


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


@router.get("/api/jobs/{job_id}/video")
async def get_job_video(
    job_id: str,
    config: AppConfig = Depends(get_config),
) -> FileResponse:
    service = LectureExportService(config)
    video_path = service.get_video_export_path(job_id)
    if video_path is None:
        raise HTTPException(status_code=404, detail="Exported video not found.")
    return FileResponse(
        path=video_path,
        filename=f"{job_id}_lecture_with_script.mp4",
        media_type="video/mp4",
    )


@router.post("/api/jobs/{job_id}/export-video")
async def export_job_video(
    job_id: str,
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    service = LectureExportService(config)
    try:
        return await asyncio.to_thread(service.export_job_video, job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found.") from None
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("lecture_export.failed job_id=%s", job_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
    skip_generation: bool = Query(False, description="Forwarded to SlideParser API."),
    previous_context: Optional[str] = Query(
        None,
        description="Compatibility-only; ignored in current upstream flow.",
    ),
    render_dpi: Optional[int] = Query(None, ge=72, le=600),
    tts_provider: Optional[str] = Query(
        None,
        pattern="^(openai|elevenlabs)$",
        description="Optional transcript TTS provider override for this job.",
    ),
    tts_model: Optional[str] = Query(
        None,
        min_length=1,
        max_length=128,
        description="Optional transcript TTS model override for this job.",
    ),
    tts_voice: Optional[str] = Query(
        None,
        min_length=1,
        max_length=128,
        description="Optional transcript TTS voice override for this job.",
    ),
    tts_elevenlabs_output_format: Optional[str] = Query(
        None,
        min_length=1,
        max_length=64,
        description="Optional ElevenLabs output format override for this job.",
    ),
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
        tts_provider=tts_provider,
        tts_model=tts_model,
        tts_voice=tts_voice,
        tts_elevenlabs_output_format=tts_elevenlabs_output_format,
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
        logger.exception("process_pdf.failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
