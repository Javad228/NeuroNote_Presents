"""Compatibility entrypoint.

Preferred app import: backend.main:app
This module keeps uvicorn app:app working.
"""

from pathlib import Path

from backend.main import app


if __name__ == "__main__":
    import uvicorn

    base_dir = Path(__file__).resolve().parent
    reload_dirs = [base_dir / "backend", base_dir / "frontend-next", base_dir / "frontend"]
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8100,
        reload=True,
        reload_dirs=[str(path) for path in reload_dirs if path.exists()],
        # Job artifacts, rendered slides, audio, and QA cache writes can trigger a reload mid-request.
        # Keep the dev reloader focused on source files.
        reload_excludes=[
            "jobs/*",
            "jobs/**",
            "*.wav",
            "*.mp3",
            "*.m4a",
            "*.ogg",
        ],
    )
