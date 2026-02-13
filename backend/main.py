from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import get_config


BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"


def create_app() -> FastAPI:
    app = FastAPI(
        title="NeuroNote Presents Orchestrator",
        description="Upload lecture PDFs, chunk slides, and run NeuroNote per chunk.",
        version="0.2.0",
    )

    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.on_event("startup")
    async def ensure_directories() -> None:
        config = get_config()
        config.jobs_root.mkdir(parents=True, exist_ok=True)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/new-project")
    async def new_project() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "new-project.html")

    @app.get("/lecture/{job_id}")
    async def lecture(job_id: str) -> FileResponse:
        return FileResponse(FRONTEND_DIR / "lecture.html")

    return app


app = create_app()
