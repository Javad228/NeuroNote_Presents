"""Compatibility entrypoint.

Preferred app import: backend.main:app
This module keeps uvicorn app:app working.
"""

from backend.main import app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8100, reload=True)
