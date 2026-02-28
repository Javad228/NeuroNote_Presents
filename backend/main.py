import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import get_config

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
_DEV_LOOPBACK_ORIGIN_REGEX = r"^https?://(?:(?:localhost)|(?:127\.0\.0\.1)|(?:\[::1\]))(?::\d+)?$"

if callable(load_dotenv):
    load_dotenv(BASE_DIR / ".env", override=False)


def configure_logging() -> None:
    # Keep backend logs visible even when runner defaults are stricter than INFO.
    level_name = os.environ.get("APP_LOG_LEVEL", os.environ.get("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.getLogger("backend").setLevel(level)

    # If app is launched outside uvicorn logging config, install a minimal handler.
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    # Azure SDK HTTP request/response dumps are very noisy at INFO.
    azure_level_name = os.environ.get("AZURE_SDK_LOG_LEVEL", "WARNING").upper()
    azure_level = getattr(logging, azure_level_name, logging.WARNING)
    for logger_name in (
        "azure",
        "azure.core",
        "azure.core.pipeline",
        "azure.core.pipeline.policies.http_logging_policy",
    ):
        logging.getLogger(logger_name).setLevel(azure_level)


def create_app() -> FastAPI:
    configure_logging()
    config = get_config()
    app = FastAPI(
        title="NeuroNote Presents Orchestrator",
        description="Upload lecture PDFs, chunk slides, and run NeuroNote per chunk.",
        version="0.2.0",
    )

    if config.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_allow_origins,
            # Accept common loopback aliases in local dev even when CORS_ALLOW_ORIGINS
            # is set to only one hostname variant (e.g. localhost vs 127.0.0.1).
            allow_origin_regex=_DEV_LOOPBACK_ORIGIN_REGEX,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=".*",
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(router)
    legacy_frontend_available = FRONTEND_DIR.is_dir()
    if legacy_frontend_available:
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.on_event("startup")
    async def ensure_directories() -> None:
        config.jobs_root.mkdir(parents=True, exist_ok=True)

    if legacy_frontend_available:

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(FRONTEND_DIR / "index.html")

        @app.get("/new-project")
        async def new_project() -> FileResponse:
            return FileResponse(FRONTEND_DIR / "new-project.html")

        @app.get("/lecture/{job_id}")
        async def lecture(job_id: str) -> FileResponse:
            return FileResponse(FRONTEND_DIR / "lecture.html")
    else:

        @app.get("/")
        async def index() -> dict[str, str]:
            return {
                "service": "NeuroNote Presents Orchestrator",
                "frontend": "Use the Next.js app in frontend-next/",
                "health": "/healthz",
                "docs": "/docs",
            }

    return app


app = create_app()
