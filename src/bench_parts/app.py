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

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from bench_parts.db import connect
from bench_parts.forms import (
    cents_to_dollars_field,
    parse_count,
    parse_dollars_to_cents,
    parse_signed_count,
)
from bench_parts.schema import initialise
from bench_parts.store import Part, PartStore
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


#: Every field the add/edit forms post. Used to echo submitted values back on a
#: rejection, so nothing typed is lost.
_PART_FIELDS = (
    "mpn",
    "manufacturer",
    "description",
    "category",
    "quantity_on_hand",
    "min_quantity",
    "unit_cost",
    "supplier",
    "supplier_url",
    "location",
    "notes",
)


def _render_part_form(
    request: Request,
    values: dict[str, str] | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render the new-part form, optionally repopulated with a rejected attempt."""
    return templates.TemplateResponse(
        request=request,
        name="part_form.html",
        context={
            "values": {name: (values or {}).get(name, "") for name in _PART_FIELDS},
            "error": error,
        },
        status_code=status_code,
    )


@app.get("/parts/new", response_class=HTMLResponse)
def new_part_form(request: Request) -> HTMLResponse:
    """The add-a-part form."""
    return _render_part_form(request)


@app.post("/parts")
def create_part(
    request: Request,
    store: StoreDep,
    mpn: Annotated[str, Form()] = "",
    manufacturer: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    category: Annotated[str, Form()] = "",
    quantity_on_hand: Annotated[str, Form()] = "",
    min_quantity: Annotated[str, Form()] = "",
    unit_cost: Annotated[str, Form()] = "",
    supplier: Annotated[str, Form()] = "",
    supplier_url: Annotated[str, Form()] = "",
    location: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> Response:
    """Create a part from the submitted form.

    **Redirects rather than rendering.** Showing the new part straight from the
    POST means a refresh re-submits — and on a tablet being handed around,
    that's a realistic way to end up with the same part twice.

    Every value arrives as a string, and untouched optional fields arrive as
    ``""``; the store normalises those to ``None``. Number and money parsing
    happen here (``bench_parts.forms``) so the store never sees a float.

    A rejection hands the form back with everything still in it — the person
    is at a bench; making them re-type a supplier URL because they forgot the
    description is a worse failure than the validation error itself.
    """
    submitted = {
        "mpn": mpn,
        "manufacturer": manufacturer,
        "description": description,
        "category": category,
        "quantity_on_hand": quantity_on_hand,
        "min_quantity": min_quantity,
        "unit_cost": unit_cost,
        "supplier": supplier,
        "supplier_url": supplier_url,
        "location": location,
        "notes": notes,
    }

    try:
        part = store.create_part(
            mpn=mpn,
            manufacturer=manufacturer,
            description=description,
            category=category,
            quantity_on_hand=parse_count(quantity_on_hand, "opening quantity"),
            min_quantity=parse_count(min_quantity, "minimum quantity"),
            unit_cost=parse_dollars_to_cents(unit_cost),
            supplier=supplier,
            supplier_url=supplier_url,
            location=location,
            notes=notes,
        )
    except ValueError as exc:
        return _render_part_form(request, values=submitted, error=str(exc), status_code=400)

    return RedirectResponse(url=f"/parts/{part.id}", status_code=303)


#: The catalogue fields the edit form can change. Deliberately excludes
#: ``quantity_on_hand`` — stock moves only through movements — so a smuggled
#: quantity in the POST body is simply never read.
_EDIT_FIELDS = tuple(f for f in _PART_FIELDS if f != "quantity_on_hand")


def _edit_values(part: Part) -> dict[str, str]:
    """The part as the edit form wants it: strings, cost in dollars."""
    return {
        "mpn": part.mpn or "",
        "manufacturer": part.manufacturer or "",
        "description": part.description,
        "category": part.category.value,
        "min_quantity": str(part.min_quantity),
        "unit_cost": cents_to_dollars_field(part.unit_cost),
        "supplier": part.supplier or "",
        "supplier_url": part.supplier_url or "",
        "location": part.location or "",
        "notes": part.notes or "",
    }


def _render_detail(
    request: Request,
    store: PartStore,
    part: Part,
    values: dict[str, str] | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """The detail page. On a rejected edit, ``values`` carries the attempt so
    nothing typed is lost — and the part's own info and history still render,
    rather than the page collapsing to a bare form."""
    return templates.TemplateResponse(
        request=request,
        name="part_detail.html",
        context={
            "part": part,
            "movements": store.list_movements(part.id),
            "values": values or _edit_values(part),
            "error": error,
        },
        status_code=status_code,
    )


@app.get("/parts/{part_id}", response_class=HTMLResponse)
def part_detail(request: Request, part_id: int, store: StoreDep) -> HTMLResponse:
    """Everything about one part: the count, the catalogue fields (editable),
    and the movement history."""
    return _render_detail(request, store, _require_part(store, part_id))


@app.post("/parts/{part_id}")
def edit_part(
    request: Request,
    part_id: int,
    store: StoreDep,
    mpn: Annotated[str, Form()] = "",
    manufacturer: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    category: Annotated[str, Form()] = "",
    min_quantity: Annotated[str, Form()] = "",
    unit_cost: Annotated[str, Form()] = "",
    supplier: Annotated[str, Form()] = "",
    supplier_url: Annotated[str, Form()] = "",
    location: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> Response:
    """Edit the catalogue fields. **Cannot change stock.**

    There is no ``quantity_on_hand`` parameter here on purpose: FastAPI simply
    never reads one from the body, so a client that smuggles it in changes
    nothing. Stock moves through the movement controls, which write a ledger
    row. ``min_quantity`` *is* editable — a threshold is policy, not a count.
    """
    part = _require_part(store, part_id)
    submitted = {
        "mpn": mpn,
        "manufacturer": manufacturer,
        "description": description,
        "category": category,
        "min_quantity": min_quantity,
        "unit_cost": unit_cost,
        "supplier": supplier,
        "supplier_url": supplier_url,
        "location": location,
        "notes": notes,
    }

    try:
        store.update_part(
            part.id,
            mpn=mpn,
            manufacturer=manufacturer,
            description=description,
            category=category,
            min_quantity=parse_count(min_quantity, "reorder threshold"),
            unit_cost=parse_dollars_to_cents(unit_cost),
            supplier=supplier,
            supplier_url=supplier_url,
            location=location,
            notes=notes,
        )
    except ValueError as exc:
        return _render_detail(
            request, store, part, values=submitted, error=str(exc), status_code=400
        )

    return RedirectResponse(url=f"/parts/{part.id}", status_code=303)


def _require_part(store: PartStore, part_id: int) -> Part:
    part = store.get_part(part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="no such part")
    return part


@app.post("/parts/{part_id}/movements")
def record_movement(
    request: Request,
    part_id: int,
    store: StoreDep,
    action: Annotated[str, Form()] = "",
    amount: Annotated[str, Form()] = "",
    ticket_ref: Annotated[str, Form()] = "",
) -> Response:
    """Restock, consume, or correct — the only way the UI moves a count.

    Each action writes a ledger row through the store, which moves the cached
    quantity in the same transaction. ``restock`` and ``consume`` take a
    positive amount (``consume`` negates it — a caller can't add stock by
    passing a negative to "use"); ``correct`` takes a signed one.

    A rejection — taking more than is on hand, a zero amount, an unknown
    action — comes back as the detail page with a readable message and
    nothing written, rather than a 500.
    """
    part = _require_part(store, part_id)

    try:
        if action == "restock":
            store.restock(part.id, parse_count(amount, "amount", allow_zero=False))
        elif action == "consume":
            store.consume(
                part.id,
                parse_count(amount, "amount", allow_zero=False),
                ticket_ref=ticket_ref,
            )
        elif action == "correct":
            store.correct(part.id, parse_signed_count(amount, "correction"))
        else:
            raise ValueError(f"unknown action {action!r} — expected restock, consume or correct")
    except ValueError as exc:
        return _render_detail(request, store, part, error=str(exc), status_code=400)

    return RedirectResponse(url=f"/parts/{part.id}", status_code=303)


@app.get("/order", response_class=HTMLResponse)
def order_list(request: Request, store: StoreDep) -> HTMLResponse:
    """The "to order" list — everything at or below its reorder threshold.

    Most urgent first: out before low, then by how far under. Reads
    ``store.list_below_min()``, which shares its definition of "low" with the
    catalogue's colours, so the two pages cannot disagree about a part.
    """
    return templates.TemplateResponse(
        request=request,
        name="order.html",
        context={"parts": store.list_below_min()},
    )
