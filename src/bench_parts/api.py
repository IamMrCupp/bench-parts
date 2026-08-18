"""The JSON API — a consumer-agnostic face over the same store the web UI uses.

benchhud is the first consumer (via an ``InventorySource`` adapter in its own
repo), but nothing here is benchhud-specific: a dashboard, a widget, or a shell
script gets the same endpoints. There is **no second data model** — reads
project ``bench_parts.store`` dataclasses, and the write endpoint calls the
same ``store.consume()`` the detail page does, so a movement recorded from the
HUD is indistinguishable from one recorded by hand.

**Auth** is the ``/api/`` branch of the middleware in ``bench_parts.app``:
``Authorization: Bearer <BENCH_PARTS_API_TOKEN>`` *or* a browser session, else
``401`` JSON — never a redirect to an HTML login page a service can't use.

**Shape stability** is the projection's job. Unlike intake, there is no PII to
filter (parts data is stream-safe); ``to_api_part`` exists so the JSON has one
definition, and so ``status`` is derived exactly as the UI derives it.
"""

# No `from __future__ import annotations` here on purpose: the routes are built
# inside a closure, and FastAPI resolves *string* annotations against module
# globals — a locally-bound `Depends(get_store)` would silently become a query
# parameter and every call would 422. Eager annotations keep the closure honest.

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from bench_parts.store import Movement, Part, PartStore

#: The keys every Part JSON object carries — the contract a consumer codes to.
PART_KEYS = (
    "id",
    "mpn",
    "manufacturer",
    "description",
    "category",
    "category_label",
    "quantity_on_hand",
    "min_quantity",
    "status",
    "unit_cost_cents",
    "supplier",
    "supplier_url",
    "location",
    "notes",
    "created_at",
    "updated_at",
    "url",
)

MOVEMENT_KEYS = (
    "id",
    "part_id",
    "delta",
    "reason",
    "ticket_ref",
    "unit_cost_cents_at_time",
    "created_at",
)


def to_api_part(part: Part, base_url: str) -> dict[str, object]:
    """Project a store ``Part`` to its JSON shape. Pure — no request needed.

    ``status`` comes from the same derivation the catalogue colours use, so a
    consumer showing "low" agrees with the tablet showing amber. Cost is named
    ``unit_cost_cents`` so nobody has to guess the unit; it is always an
    integer or ``null``, never a float.
    """
    return {
        "id": part.id,
        "mpn": part.mpn,
        "manufacturer": part.manufacturer,
        "description": part.description,
        "category": part.category.value,
        "category_label": part.category.label,
        "quantity_on_hand": part.quantity_on_hand,
        "min_quantity": part.min_quantity,
        "status": part.status.value,
        "unit_cost_cents": part.unit_cost,
        "supplier": part.supplier,
        "supplier_url": part.supplier_url,
        "location": part.location,
        "notes": part.notes,
        "created_at": part.created_at,
        "updated_at": part.updated_at,
        # A deep link into the web UI, behind its own login. Handy from a HUD.
        "url": f"{base_url.rstrip('/')}/parts/{part.id}",
    }


def to_api_movement(movement: Movement) -> dict[str, object]:
    return {
        "id": movement.id,
        "part_id": movement.part_id,
        "delta": movement.delta,
        "reason": movement.reason.value,
        "ticket_ref": movement.ticket_ref,
        "unit_cost_cents_at_time": movement.unit_cost_at_time,
        "created_at": movement.created_at,
    }


def build_router(get_store) -> APIRouter:
    """The API routes, bound to the app's store dependency.

    Built by a function rather than at import so this module doesn't import
    ``bench_parts.app`` (which would be circular) — the app passes its own
    ``get_store`` in.
    """
    router = APIRouter(prefix="/api")
    StoreDep = Annotated[PartStore, Depends(get_store)]

    def _require_part(store: PartStore, part_id: int) -> Part:
        part = store.get_part(part_id)
        if part is None:
            raise HTTPException(status_code=404, detail="no such part")
        return part

    @router.get("/parts")
    def list_parts(
        request: Request,
        store: StoreDep,
        below_min: bool = False,
        q: str = "",
        category: str = "",
    ) -> list[dict[str, object]]:
        """The catalogue, or with ``?below_min=1`` the "to order" set.

        ``below_min`` uses ``store.list_below_min()`` — the same call the order
        page makes, most-urgent-first — so a consumer's low list is exactly the
        one on the tablet. ``q`` and ``category`` narrow like the catalogue
        does; an unknown category is a 400, not an empty list.
        """
        try:
            parts = (
                store.list_below_min()
                if below_min
                else store.search(q=q or None, category=category or None)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        base = str(request.base_url)
        return [to_api_part(p, base) for p in parts]

    @router.get("/parts/{part_id}")
    def get_part(request: Request, part_id: int, store: StoreDep) -> dict[str, object]:
        return to_api_part(_require_part(store, part_id), str(request.base_url))

    @router.get("/parts/{part_id}/movements")
    def part_movements(part_id: int, store: StoreDep) -> list[dict[str, object]]:
        """This part's ledger, oldest first."""
        _require_part(store, part_id)
        return [to_api_movement(m) for m in store.list_movements(part_id)]

    @router.get("/movements")
    def movements_by_ticket(
        store: StoreDep,
        ticket_ref: Annotated[str, Query()] = "",
    ) -> list[dict[str, object]]:
        """Everything used on one job — the costing query.

        ``ticket_ref`` is required: dumping the whole ledger isn't a use case,
        and an unfiltered call is more likely a bug than an intent. A ref this
        service has never seen is an empty list, not a 404 — bench-parts never
        validates refs against intake.
        """
        if not ticket_ref.strip():
            raise HTTPException(status_code=400, detail="ticket_ref is required")
        return [to_api_movement(m) for m in store.list_movements_by_ticket(ticket_ref)]

    return router
