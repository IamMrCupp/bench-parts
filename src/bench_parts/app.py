"""FastAPI application for bench-parts.

The standalone web app: a tablet-friendly catalogue over the parts store. The
REST API (Phase 4) and the auth gate (Phase 3) layer on top of this; nothing
here assumes either exists yet.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
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


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create or migrate the database before the first request.

    ``get_store()`` also calls ``initialise()``, so the app works without this —
    but lazily, on the first store-touching request, and ``/healthz`` doesn't
    touch the store. Seen on the v5 roll: pod Running, ``user_version`` still 4
    until someone clicked. Doing it here means a broken migration fails the pod
    at boot, in the logs, instead of on the first tap at the bench.
    """
    with connect(get_db_path()) as conn:
        initialise(conn)
    yield


app = FastAPI(
    title="bench-parts",
    description="Self-hosted parts inventory for a board-repair bench. LAN-only.",
    lifespan=_lifespan,
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
EXEMPT_PATHS = frozenset({"/healthz", "/login", "/manifest.webmanifest"})

#: Path *prefixes* reachable without a session. Icons, the manifest and the
#: one script are fetched by the browser before anyone has signed in — a
#: redirect to /login there means no icon and a broken "Add to Home Screen".
#: Static files only; nothing under here reads the database.
EXEMPT_PREFIXES = ("/static/",)

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
    exempt = path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES)
    if not exempt and not request.session.get("authenticated"):
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
            return _error_page(request, 403, "Request could not be verified.")

    return await call_next(request)


#: Response headers on every response. Inline CSS is the whole styling story
#: (no build step, no external stylesheet to forget to deploy), so styles get
#: 'unsafe-inline' deliberately; scripts do NOT — the one script lives at
#: /static/app.js, which is what lets an injected <script> in a description be
#: inert. frame-ancestors 'none' + X-Frame-Options DENY: nothing embeds this.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
    ),
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


# Added last, so it wraps the auth middleware — the session has to be decoded
# before anything can check it. `secret_key` is resolved at import: a
# deployment without a password hash fails here, visibly, rather than starting.
app.add_middleware(
    SessionMiddleware,
    secret_key=auth.session_secret(),
    session_cookie="session",
    # 30 days: a shop tablet, not a bank. Starlette's default is 14; explicit
    # so it's a decision rather than an inheritance.
    max_age=30 * 24 * 60 * 60,
    https_only=auth.cookie_secure(),
    same_site="lax",
)

# Resolved relative to this module, not the working directory — the app runs
# from a container image where cwd is not the repo root.
TEMPLATES_DIR = Path(__file__).parent / "templates"


def flash(request: Request, message: str) -> None:
    """Queue a one-shot message for the *next* rendered page.

    Every mutation here is POST → redirect → GET, so the confirmation has to
    survive one hop. It rides in the signed session and is consumed on render
    (see ``_page_context``), so a refresh doesn't repeat it and it can't be
    replayed by a bookmark.
    """
    request.session["flash"] = message


def _page_context(request: Request) -> dict[str, object]:
    """Per-render context every template gets: CSRF token, auth state, the
    pending flash (consumed here), and the low-stock count for the nav.

    A context processor rather than a per-route context entry, for the same
    deny-by-default reason as the auth middleware: a template added later gets
    the token without anyone remembering to pass it, and a form that omits the
    hidden field fails visibly at the first POST instead of silently dropping
    protection.
    """
    authenticated = bool(request.session.get("authenticated"))
    # Cheap at bench scale (hundreds of parts), and it's the number you glance
    # at without clicking. Only computed for a signed-in page — the login page
    # must not leak how much is low.
    low_count = 0
    if authenticated:
        try:
            with connect(get_db_path()) as conn:
                initialise(conn)
                low_count = len(PartStore(conn).list_below_min())
        except Exception:  # the nav must never take a page down
            low_count = 0
    return {
        "csrf_token": auth.csrf_token(request.session),
        # Distinct from the token: a token exists *before* sign-in, because the
        # login form itself needs one. Keying UI on the token would show a
        # "Sign out" button to someone who has never signed in.
        "authenticated": authenticated,
        "flash": request.session.pop("flash", None),
        "low_count": low_count,
    }


templates = Jinja2Templates(directory=TEMPLATES_DIR, context_processors=[_page_context])

# Icons, the manifest and the one script. Resolved relative to this module,
# like the templates, and shipped inside the wheel (see pyproject). Mounted
# under /static; the manifest is also served at the root because some browsers
# want it there and it's referenced from every page.
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> Response:
    return Response(
        (STATIC_DIR / "manifest.webmanifest").read_bytes(),
        media_type="application/manifest+json",
    )


# --- error pages -------------------------------------------------------------
#
# FastAPI answers every HTTPException with JSON. That's right for /api/* and
# wrong for a person on a tablet, who should never see {"detail": ...}. These
# handlers keep JSON for the API and render error.html — in the app's own
# chrome, with a way back — for everything else.

_ERROR_TITLES = {
    400: "That didn't work",
    403: "Couldn't verify that request",
    404: "Not found",
    500: "Something broke",
}


def _wants_json(request: Request) -> bool:
    return request.url.path.startswith("/api/")


def _error_page(request: Request, status: int, detail: str) -> HTMLResponse:
    hint = {
        403: "Your session may have expired. Reload the page and try again.",
        404: "That part or page doesn't exist — it may have been deleted.",
        500: "It's been logged. Try again; if it keeps happening, restart the app.",
    }.get(status, "")
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={
            "status": status,
            "title": _ERROR_TITLES.get(status, "Error"),
            "detail": detail,
            "hint": hint,
        },
        status_code=status,
    )


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    if _wants_json(request):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return _error_page(request, exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    if _wants_json(request):
        return JSONResponse({"detail": jsonable_encoder(exc.errors())}, status_code=422)
    return _error_page(request, 400, "The form couldn't be read. Go back and try again.")


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
    flash(request, "Signed out")
    return RedirectResponse(url="/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def catalogue(
    request: Request,
    store: StoreDep,
    q: str = "",
    category: str = "",
    project: str = "",
    show_retired: str = "",
) -> HTMLResponse:
    """The catalogue — the primary screen.

    Every part, colour-coded by stock status, narrowed by ``?q=`` (MPN or
    description, case-insensitive substring) and ``?category=``. An unknown
    category is a 400: a typo'd filter that silently shows an empty shelf is
    worse than an error.
    """
    include_retired = show_retired not in ("", "0")
    try:
        parts = store.search(
            q=q or None,
            category=category or None,
            project=project or None,
            include_retired=include_retired,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    # Offer the "show retired" link only when there's something to show — an
    # empty toggle is noise on a screen you glance at.
    retired_count = (
        0
        if include_retired
        else sum(1 for p in store.list_parts(include_retired=True) if p.retired)
    )

    return templates.TemplateResponse(
        request=request,
        name="catalogue.html",
        context={
            "parts": parts,
            "q": q,
            "category": category,
            "project": project,
            "projects": store.list_projects(),
            "show_retired": include_retired,
            "retired_count": retired_count,
        },
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
    "project",
    "supplier_pn",
    "reorder_qty",
)


def _render_part_form(
    request: Request,
    store: PartStore,
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
            "projects": store.list_projects(),
        },
        status_code=status_code,
    )


@app.get("/parts/new", response_class=HTMLResponse)
def new_part_form(request: Request, store: StoreDep) -> HTMLResponse:
    """The add-a-part form."""
    return _render_part_form(request, store)


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
    project: Annotated[str, Form()] = "",
    supplier_pn: Annotated[str, Form()] = "",
    reorder_qty: Annotated[str, Form()] = "",
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
        "project": project,
        "supplier_pn": supplier_pn,
        "reorder_qty": reorder_qty,
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
            project=project,
            supplier_pn=supplier_pn,
            reorder_qty=parse_count(reorder_qty, "reorder quantity") or None,
        )
    except ValueError as exc:
        return _render_part_form(request, store, values=submitted, error=str(exc), status_code=400)

    flash(request, f"Added {part.description}")
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
        "project": part.project or "",
        "supplier_pn": part.supplier_pn or "",
        "reorder_qty": str(part.reorder_qty) if part.reorder_qty else "",
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
    movements = store.list_movements(part.id)
    return templates.TemplateResponse(
        request=request,
        name="part_detail.html",
        context={
            "part": part,
            "movements": movements,
            # Delete is only *possible* with no consumption history; don't
            # offer a button that will only ever say no.
            "deletable": not any(m.reason.value == "consume" for m in movements),
            "values": values or _edit_values(part),
            "error": error,
            "projects": store.list_projects(),
        },
        status_code=status_code,
    )


def qr_svg(data: str) -> str:
    """A QR code as an inline SVG string.

    Server-side and dependency-light on purpose: the shop LAN may have no WAN,
    so a client-side QR library from a CDN is exactly the kind of dependency
    this app refuses. ``segno`` is pure Python with zero transitive deps.
    ``svg_inline`` emits a fragment made for embedding straight into HTML.
    """
    import segno

    # omitsize swaps the fixed width/height attrs for a viewBox — without it
    # the CSS box scales but the drawing stays at its intrinsic module count,
    # leaving a tiny code in the corner of the label.
    return segno.make(data, error="m").svg_inline(border=0, omitsize=True)


@app.get("/parts/{part_id}/label", response_class=HTMLResponse)
def part_label(request: Request, part_id: int, store: StoreDep) -> HTMLResponse:
    """A printable drawer label: description, MPN, location, and a QR that
    opens this part. The QR encodes the URL the request arrived on, so a label
    printed from the LAN address scans back to the LAN address."""
    part = _require_part(store, part_id)
    target = str(request.base_url).rstrip("/") + f"/parts/{part.id}"
    return templates.TemplateResponse(
        request,
        "label.html",
        {**_page_context(request), "part": part, "qr": qr_svg(target)},
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
    project: Annotated[str, Form()] = "",
    supplier_pn: Annotated[str, Form()] = "",
    reorder_qty: Annotated[str, Form()] = "",
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
        "project": project,
        "supplier_pn": supplier_pn,
        "reorder_qty": reorder_qty,
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
            project=project,
            supplier_pn=supplier_pn,
            reorder_qty=parse_count(reorder_qty, "reorder quantity") or None,
        )
    except ValueError as exc:
        return _render_detail(
            request, store, part, values=submitted, error=str(exc), status_code=400
        )

    flash(request, "Saved")
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
    next: Annotated[str, Form()] = "",
) -> Response:
    """Restock, consume, or correct — the only way the UI moves a count.

    ``next`` lets the order page log a delivery and come straight back to the
    list; it goes through ``_safe_next`` so it can only ever be a path here.

    Each action writes a ledger row through the store, which moves the cached
    quantity in the same transaction. ``restock`` and ``consume`` take a
    positive amount (``consume`` negates it — a caller can't add stock by
    passing a negative to "use"); ``correct`` takes a signed one.

    A rejection — taking more than is on hand, a zero amount, an unknown
    action — comes back as the detail page with a readable message and
    nothing written, rather than a 500.
    """
    part = _require_part(store, part_id)
    name = part.mpn or part.description

    try:
        if action == "restock":
            n = parse_count(amount, "amount", allow_zero=False)
            updated = store.restock(part.id, n)
            flash(request, f"Restocked {n} {name} — now {updated.quantity_on_hand} on hand")
        elif action == "consume":
            n = parse_count(amount, "amount", allow_zero=False)
            updated = store.consume(part.id, n, ticket_ref=ticket_ref)
            job = f" on {ticket_ref.strip()}" if ticket_ref.strip() else ""
            flash(request, f"Used {n} {name}{job} — {updated.quantity_on_hand} left")
        elif action == "correct":
            n = parse_signed_count(amount, "correction")
            updated = store.correct(part.id, n)
            flash(request, f"Corrected {name} by {n:+d} — now {updated.quantity_on_hand} on hand")
        else:
            raise ValueError(f"unknown action {action!r} — expected restock, consume or correct")
    except ValueError as exc:
        return _render_detail(request, store, part, error=str(exc), status_code=400)

    target = _safe_next(next) if next.strip() else f"/parts/{part.id}"
    if target == "/":  # _safe_next's fallback for a hostile value — go to the part instead
        target = f"/parts/{part.id}"
    return RedirectResponse(url=target, status_code=303)


@app.get("/order", response_class=HTMLResponse)
def order_list(request: Request, store: StoreDep) -> HTMLResponse:
    """The "to order" list as baskets — one per supplier, most urgent first
    inside each. Each API-able supplier (Digi-Key, Mouser) gets a paste-ready
    ``number,qty`` block for its BOM tool; Amazon rows link out; everything can
    be restocked right from the row once it arrives.

    Reads ``store.below_min_by_supplier()``, which sits on the same definition
    of "low" as the catalogue's colours, so the two pages cannot disagree.
    """
    groups = store.below_min_by_supplier()
    return templates.TemplateResponse(
        request=request,
        name="order.html",
        context={
            "groups": groups,
            "total": sum(len(g.parts) for g in groups),
            "bom_tools": _BOM_TOOLS,
        },
    )


#: Where each supplier's paste-a-BOM tool lives. Keyed case-folded. A supplier
#: not listed here gets the bulk-add text without a link (still pasteable).
_BOM_TOOLS = {
    "digi-key": "https://www.digikey.com/en/mylists/list/bulk-add",
    "digikey": "https://www.digikey.com/en/mylists/list/bulk-add",
    "mouser": "https://www.mouser.com/Tools/BOM/",
}


@app.get("/order.csv")
def order_csv(store: StoreDep) -> Response:
    """The order list as a CSV download — your data, out."""
    return Response(
        store.order_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="bench-parts-order.csv"'},
    )


@app.post("/parts/{part_id}/retire")
def retire_part(request: Request, part_id: int, store: StoreDep) -> Response:
    """Soft-delete: hide a part you no longer stock, keep its history."""
    part = _require_part(store, part_id)
    store.retire_part(part.id)
    flash(request, f"Retired {part.mpn or part.description} — history kept")
    return RedirectResponse(url=f"/parts/{part_id}", status_code=303)


@app.post("/parts/{part_id}/unretire")
def unretire_part(request: Request, part_id: int, store: StoreDep) -> Response:
    part = _require_part(store, part_id)
    store.unretire_part(part.id)
    flash(request, f"{part.mpn or part.description} is back in stock")
    return RedirectResponse(url=f"/parts/{part_id}", status_code=303)


@app.post("/parts/{part_id}/delete")
def delete_part(request: Request, part_id: int, store: StoreDep) -> Response:
    """Hard-delete a mis-add. Refused (400, page intact) once anything has been
    consumed — that's job history; retire instead."""
    part = _require_part(store, part_id)
    try:
        store.delete_part(part.id)
    except ValueError as exc:
        return _render_detail(request, store, part, error=str(exc), status_code=400)
    flash(request, f"Deleted {part.mpn or part.description}")
    return RedirectResponse(url="/", status_code=303)


@app.get("/activity", response_class=HTMLResponse)
def activity(request: Request, store: StoreDep, ticket_ref: str = "") -> HTMLResponse:
    """The ledger, newest first, across every part.

    Answers "what did I do yesterday" and — with ``?ticket_ref=`` — "what went
    into 26-0001 and what did it cost", without the API.
    """
    ref = ticket_ref.strip()
    rows = store.list_recent_movements(limit=200, ticket_ref=ref or None)
    return templates.TemplateResponse(
        request=request,
        name="activity.html",
        context={
            "rows": rows,
            "ticket_ref": ref,
            "spend_cents": store.ticket_spend_cents(ref) if ref else None,
        },
    )


@app.get("/export.csv")
def export_parts(store: StoreDep) -> Response:
    """Every part, every field — retired included and flagged."""
    return Response(
        store.export_parts_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="bench-parts.csv"'},
    )


@app.get("/export/movements.csv")
def export_movements(store: StoreDep) -> Response:
    """The whole ledger — the parts alone aren't the data."""
    return Response(
        store.export_movements_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="bench-parts-movements.csv"'},
    )
