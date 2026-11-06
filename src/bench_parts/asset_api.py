"""The JSON API for durable assets — the face benchhud reads.

benchhud's interface monitor asks "what gear is tied to this engagement, on what
port"; the read side answers the first half (`?assigned_ref=`), and assign/return
let the HUD (or a script) move gear onto and off an engagement. Same auth as the
parts API — the `/api/` branch of the middleware in `bench_parts.app`: a bearer
token *or* a browser session, else 401 JSON. Asset data, like parts data, carries
no PII, so there's nothing to filter — `to_api_asset` exists only so the JSON has
one definition.

There is no second data model: reads project `bench_parts.asset_store` dataclasses,
and the write endpoints call the same `store.assign()` / `store.return_asset()` the
web UI does, so a move recorded from the HUD is indistinguishable from one recorded
by hand.
"""

# No `from __future__ import annotations` — like api.py, FastAPI resolves string
# annotations against module globals, and a locally-bound Depends would silently
# become a query parameter.

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictStr

from bench_parts.asset_store import Asset, AssetMovement, AssetStore


class AssignRequest(BaseModel):
    """Body for ``POST /api/assets/{id}/assign``.

    ``extra="forbid"`` turns a typo'd key into a 422; strict types reject
    coercion at the edge, the same contract as the parts API's bodies.
    """

    model_config = ConfigDict(extra="forbid")

    engagement_ref: StrictStr
    note: StrictStr | None = None


class ReturnRequest(BaseModel):
    """Body for ``POST /api/assets/{id}/return`` — a note is the only field.

    An empty JSON object (``{}``) is valid: returning gear needs no note. A JSON
    body is still required, and that is the CSRF story for this write, exactly as
    for parts' ``consume`` — a cross-site form can't send ``application/json``.
    """

    model_config = ConfigDict(extra="forbid")

    note: StrictStr | None = None


#: The keys every Asset JSON object carries — the contract a consumer codes to.
ASSET_KEYS = (
    "id",
    "name",
    "asset_type",
    "asset_type_label",
    "manufacturer",
    "model",
    "serial",
    "location",
    "notes",
    "status",
    "status_label",
    "assigned_ref",
    "created_at",
    "updated_at",
    "url",
)

ASSET_MOVEMENT_KEYS = (
    "id",
    "asset_id",
    "action",
    "engagement_ref",
    "note",
    "created_at",
)


def to_api_asset(asset: Asset, base_url: str) -> dict[str, object]:
    """Project a store ``Asset`` to its JSON shape. Pure — no request needed.

    ``assigned_ref`` is non-null exactly when ``status == "assigned"`` (the DB
    enforces that pairing), which is what lets benchhud trust
    ``?assigned_ref=<ref>`` to list the gear out on an engagement.
    """
    return {
        "id": asset.id,
        "name": asset.name,
        "asset_type": asset.asset_type.value,
        "asset_type_label": asset.asset_type.label,
        "manufacturer": asset.manufacturer,
        "model": asset.model,
        "serial": asset.serial,
        "location": asset.location,
        "notes": asset.notes,
        "status": asset.status.value,
        "status_label": asset.status.label,
        "assigned_ref": asset.assigned_ref,
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
        # A deep link into the web UI, behind its own login. Handy from a HUD.
        "url": f"{base_url.rstrip('/')}/assets/{asset.id}",
    }


def to_api_asset_movement(movement: AssetMovement) -> dict[str, object]:
    return {
        "id": movement.id,
        "asset_id": movement.asset_id,
        "action": movement.action.value,
        "engagement_ref": movement.engagement_ref,
        "note": movement.note,
        "created_at": movement.created_at,
    }


def build_asset_router(get_asset_store) -> APIRouter:
    """The asset API routes, bound to the app's asset-store dependency.

    Built by a function, like the parts router, so this module doesn't import
    ``bench_parts.app`` — the app passes its own ``get_asset_store`` in.
    """
    router = APIRouter(prefix="/api")
    StoreDep = Annotated[AssetStore, Depends(get_asset_store)]

    def _require_asset(store: AssetStore, asset_id: int) -> Asset:
        asset = store.get_asset(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="no such asset")
        return asset

    @router.get("/assets")
    def list_assets(
        request: Request,
        store: StoreDep,
        asset_type: str = "",
        assigned_ref: str = "",
        status: str = "",
        include_retired: bool = False,
    ) -> list[dict[str, object]]:
        """The registry, or filtered.

        ``assigned_ref`` is the filter benchhud's interface monitor uses — the
        gear out on one engagement. ``asset_type`` and ``status`` narrow like the
        catalogue; an unknown one is a 400, not an empty list.
        """
        try:
            assets = store.list_assets(
                asset_type=asset_type or None,
                assigned_ref=assigned_ref or None,
                status=status or None,
                include_retired=include_retired,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        base = str(request.base_url)
        return [to_api_asset(a, base) for a in assets]

    @router.get("/assets/{asset_id}")
    def get_asset(request: Request, asset_id: int, store: StoreDep) -> dict[str, object]:
        return to_api_asset(_require_asset(store, asset_id), str(request.base_url))

    @router.get("/assets/{asset_id}/movements")
    def asset_movements(asset_id: int, store: StoreDep) -> list[dict[str, object]]:
        """This asset's ledger, oldest first."""
        _require_asset(store, asset_id)
        return [to_api_asset_movement(m) for m in store.list_asset_movements(asset_id)]

    @router.post("/assets/{asset_id}/assign")
    def assign(
        request: Request, asset_id: int, body: AssignRequest, store: StoreDep
    ) -> dict[str, object]:
        """Send an available asset out on an engagement. **Same write path as the UI.**

        ``engagement_ref`` is stored verbatim and never validated against
        bench-recon — loose coupling across services, as with movements' ticket
        refs. 400 if the asset isn't assignable (already out, retired).
        """
        _require_asset(store, asset_id)
        try:
            asset = store.assign(asset_id, body.engagement_ref, note=body.note)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return to_api_asset(asset, str(request.base_url))

    @router.post("/assets/{asset_id}/return")
    def return_asset(
        request: Request, asset_id: int, body: ReturnRequest, store: StoreDep
    ) -> dict[str, object]:
        """Take an assigned asset back. 400 if it isn't currently assigned."""
        _require_asset(store, asset_id)
        try:
            asset = store.return_asset(asset_id, note=body.note)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return to_api_asset(asset, str(request.base_url))

    return router
