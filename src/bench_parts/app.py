"""FastAPI application for bench-parts.

Phase 0 skeleton: a health probe and a placeholder index rendered through
Jinja2. The parts catalogue, the "to order" view, auth, and the REST API arrive
in later phases — see the working folder's plan.md.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(
    title="bench-parts",
    description="Self-hosted parts inventory for a board-repair bench. LAN-only.",
)

# Resolved relative to this module, not the working directory — the app runs
# from a container image where cwd is not the repo root.
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness/readiness probe.

    Stays **unauthenticated** when the password gate lands in Phase 3 — it's what
    Kubernetes hits, and it must answer before a session exists. It is the one
    documented exception to "all routes protected", so it must never return
    anything about stock or a job.
    """
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Placeholder landing page. The parts catalogue replaces this in Phase 2."""
    return templates.TemplateResponse(request=request, name="index.html")
