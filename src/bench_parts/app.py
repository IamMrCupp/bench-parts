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
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from bench_parts import api, auth
from bench_parts.auth import verify_password
from bench_parts.db import connect
from bench_parts.forms import (
    cents_to_dollars_field,
    parse_count,
    parse_dollars_to_cents,
    parse_signed_count,
)
from bench_parts.ratelimit import RateLimiter
from bench_parts.schema import initialise
from bench_parts.store import Part, PartStore
from bench_parts.vocabulary import Category

app = FastAPI(
    title="bench-parts",
    description="Self-hosted parts inventory for a board-repair bench. LAN-only.",
)

#: The only paths reachable without a session.
#:
#: **Deny by default.** Protection is applied to everything and lifted here,
#: rather than added per-route — the inverse arrangement means a route added in
#: a later phase (the JSON API) ships unprotected the moment somebody forgets a
#: decorator, and an inventory quietly writable by anyone on the LAN is a much
#: worse failure than a route that's visibly unreachable.
#:
#: `/healthz` is the Kubernetes probe and must answer before a session can
#: exist; `/login` obviously cannot require being logged in. Nothing else
#: belongs here, and `tests/test_auth_required.py` enumerates the routing table
#: to keep that honest.
EXEMPT_PATHS = frozenset({"/healthz", "/login"})

#: Methods that change state and therefore need a CSRF token.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Failed-login throttle. In-memory and per-process, which is fine while the
#: Deployment stays at `replicas: 1` — see bench_parts/ratelimit.py for what
#: this does and does not protect against.
login_limiter = RateLimiter()


def _client_ip(request: Request) -> str | None:
    """Best-effort client address.

    Deliberately ignores `X-Forwarded-For`: nothing terminates in front of this
    service today, so honouring a client-supplied header would let anyone reset
    their own throttle bucket by changing it. Revisit if a proxy ever lands.
    """
    return request.client.host if request.client else None


@app.middleware("http")
async def require_session(request: Request, call_next):
    """Demand a session for everything outside ``EXEMPT_PATHS``, and a valid
    CSRF token on every state-changing request.

    Both checks are deny-by-default for the same reason: a form added in a later
    phase should be safe without anyone remembering to opt it in.

    ``samesite=lax`` on the session cookie already blocks cross-site POSTs in
    current browsers, and that's most of the protection. This is the
    server-side half — one mechanism enforced by the client isn't where you
    want the only defence, particularly on whatever browser a bench tablet
    happens to be running.
    """
    path = request.url.path

    # The machine-facing API (Phase 4) is gated too — by a bearer token or a
    # session, since benchhud can't do the browser login. On failure it gets a
    # 401 status code, not a 302 to an HTML page a service can't use. Still
    # deny-by-default: this is a second gate, never an exemption.
    if path.startswith("/api/"):
        if auth.valid_api_token(request.headers.get("authorization")) or request.session.get(
            "authenticated"
        ):
            return await call_next(request)
        return JSONResponse({"detail": "unauthorized"}, status_code=401)

    # Auth first for browser routes. A request with no session has nothing worth
    # forging against, and "go and sign in" is a more useful answer than
    # "unverifiable".
    if path not in EXEMPT_PATHS and not request.session.get("authenticated"):
        target = quote(path, safe="/")
        return RedirectResponse(url=f"/login?next={target}", status_code=303)

    if request.method in _MUTATING_METHODS:
        # Reading the form here consumes the request stream, so the body has to
        # be replayed or the endpoint downstream sees an empty form and every
        # submission looks like a blank one. Intake found this the hard way:
        # without it, login failed as "wrong password" with a correct password.
        body = await request.body()

        async def _replay() -> dict:
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = _replay

        form = await request.form()
        if not auth.csrf_valid(request.session, form.get("csrf_token")):
            return PlainTextResponse(
                "Request could not be verified. Reload the page and try again.",
                status_code=403,
            )

    return await call_next(request)


# Added last, so it wraps the auth middleware — the session has to be decoded
# before anything can check it. `secret_key` is resolved at import: a
# deployment without a password hash fails here, visibly, rather than starting.
app.add_middleware(
    SessionMiddleware,
    secret_key=auth.session_secret(),
    session_cookie="session",
    https_only=auth.cookie_secure(),
    same_site="lax",
)

# Resolved relative to this module, not the working directory — the app runs
# from a container image where cwd is not the repo root.
TEMPLATES_DIR = Path(__file__).parent / "templates"


def _csrf_context(request: Request) -> dict[str, object]:
    """Make ``csrf_token`` available to every template.

    A context processor rather than a per-route context entry, for the same
    deny-by-default reason as the auth middleware: a template added later gets
    the token without anyone remembering to pass it, and a form that omits the
    hidden field fails visibly at the first POST instead of silently dropping
    protection.
    """
    return {
        "csrf_token": auth.csrf_token(request.session),
        # Distinct from the token: a token exists *before* sign-in, because the
        # login form itself needs one. Keying UI on the token would show a
        # "Sign out" button to someone who has never signed in.
        "authenticated": bool(request.session.get("authenticated")),
    }


templates = Jinja2Templates(directory=TEMPLATES_DIR, context_processors=[_csrf_context])


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

# The JSON API. Auth for it lives in `require_session` above (bearer-or-session,
# 401 JSON); the router itself only knows about parts.
app.include_router(api.build_router(get_store))


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness/readiness probe.

    Stays **unauthenticated** when the password gate lands in Phase 3 — it's what
    Kubernetes hits, and it must answer before a session exists. It is the one
    documented exception to "all routes protected", so it must never return
    anything about stock or a job.
    """
    return {"status": "ok"}


def _safe_next(target: str) -> str:
    """Reduce ``?next=`` to a path on this host, or fall back to the catalogue.

    A login URL is exactly the kind of thing that gets pasted into a message, so
    an unvalidated ``next`` turns a genuine login into a bounce to somebody
    else's page — the classic phishing shape. Only a single-slash absolute path
    is allowed: ``//host`` and ``\\\\host`` are both browser-recognised ways of
    naming another origin, and a scheme (``https:``, ``javascript:``) obviously
    leaves entirely.
    """
    target = (target or "").strip()
    if (
        not target.startswith("/")
        or target.startswith("//")
        or target.startswith("/\\")
        or ":" in target.split("/")[0]
    ):
        return "/"
    return target


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "") -> HTMLResponse:
    """The sign-in page. Reachable without a session, by definition."""
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"next": _safe_next(next), "error": None},
    )


@app.post("/login")
def login(
    request: Request,
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> Response:
    """Check the shared password and start a session.

    A failure says only that the password is wrong — never whether one is
    configured, nor anything about its shape. There's one password here; every
    extra detail narrows a guess.
    """
    client = _client_ip(request)

    if not login_limiter.allowed(client):
        # Checked before the password so a locked-out client can't keep probing,
        # and so lockout costs no scrypt work — otherwise the throttle becomes
        # its own CPU amplifier on a Pi.
        wait = login_limiter.retry_after(client)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "next": _safe_next(next),
                # Ceiling division: a 300s wait is "5 minutes", not 6.
                "error": f"Too many attempts. Try again in {-(-wait // 60)} minute(s).",
            },
            status_code=429,
            headers={"Retry-After": str(wait)},
        )

    if not verify_password(password, auth.configured_hash()):
        login_limiter.record_failure(client)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"next": _safe_next(next), "error": "That password is not right."},
            status_code=401,
        )

    login_limiter.record_success(client)
    request.session["authenticated"] = True
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/logout")
def logout(request: Request) -> Response:
    """End the session."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


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
