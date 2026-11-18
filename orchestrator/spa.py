"""Serve the built single-page app (web/dist) with a fallback to index.html for client-side routes."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.exceptions import HTTPException

MISSING = "The web app is not built yet: run `npm run build` in web/ (or `npm run dev` for development).\n"


def install_spa(app: FastAPI, dist: Path) -> None:
    """Register the catch-all LAST: every /api route above keeps priority, and unknown /api paths stay JSON 404s."""
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404)
        index = root / "index.html"
        if not index.is_file():
            return PlainTextResponse(MISSING, status_code=503)
        candidate = (root / path).resolve()
        if path and candidate.is_file() and root in candidate.parents:  # never serve anything outside dist
            return FileResponse(candidate)
        return FileResponse(index)
