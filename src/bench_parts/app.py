"""FastAPI application for bench-parts.

The standalone web app: a tablet-friendly catalogue over the parts store. The
REST API (Phase 4) and the auth gate (Phase 3) layer on top of this; nothing
here assumes either exists yet.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from bench_parts.db import connect
from bench_parts.schema import initialise
from bench_parts.store import PartStore
from bench_parts.vocabulary import Category

app = FastAPI(
    title="bench-parts",
    description="Self-hosted parts inventory for a board-repair bench. LAN-only.",
)

# Resolved relative to this module, not the working directory — the app runs
# from a container image where cwd is not the repo root.
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _dollars(cents: int | None) -> str:
    """Render integer cents as ``$1.87``. The store never sees a float; the
    conversion lives at the presentation edge and nowhere else."""
    if cents is None:
        return "—"
    return f"${cents / 100:,.2f}"


templates.env.filters["dollars"] = _dollars
# Every template gets the category list for filters and selects, in enum order.
templates.env.globals["categories"] = list(Category)


def get_db_path() -> Path:
    """Where the database lives.

    On the cluster this points into the NFS PVC; locally it defaults to a file
    beside the repo, which `.gitignore` already covers. Mirrors intake's
    ``INTAKE_DB_PATH``.
    """
    return Path(os.environ.get("BENCH_PARTS_DB_PATH", "parts.db"))


def get_store() -> Iterator[PartStore]:
    """A store for one request.

    A fresh connection per request rather than a shared one: FastAPI runs sync
    endpoints in a threadpool, and SQLite connections aren't safe to share
    across threads. Cheap for a local file, and the deployment is single-writer
    anyway.

    ``initialise()`` short-circuits on an up-to-date ``user_version``, so calling
    it here costs one read and means the database is always ready regardless of
    how the app was started.
    """
    with connect(get_db_path()) as conn:
        initialise(conn)
        yield PartStore(conn)


StoreDep = Annotated[PartStore, Depends(get_store)]


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
def catalogue(
    request: Request,
    store: StoreDep,
    q: str = "",
    category: str = "",
) -> HTMLResponse:
    """The catalogue — the primary screen.

    Every part, colour-coded by stock status, narrowed by ``?q=`` (MPN or
    description, case-insensitive substring) and ``?category=``. An unknown
    category is a 400: a typo'd filter that silently shows an empty shelf is
    worse than an error.
    """
    try:
        parts = store.search(q=q or None, category=category or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    return templates.TemplateResponse(
        request=request,
        name="catalogue.html",
        context={"parts": parts, "q": q, "category": category},
    )
