"""The asset store — durable test gear, assigned to engagements and returned.

A separate model from :mod:`bench_parts.store`'s consumable parts, and the same
discipline turned to a different shape:

**The ``asset_movements`` ledger is the source of truth; ``assets.status`` and
``assets.assigned_ref`` are a cache.** The rule that makes that true is enforced
here, not in the schema: *no code path changes an asset's status without writing
a ledger row in the same transaction*. It starts at :meth:`AssetStore.create_asset`
— registering an asset writes an ``acquire`` row, never a bare status write — and
every lifecycle method goes through :meth:`AssetStore._transition`, which writes
the row and moves the cache atomically.

Parts are *consumed* (a signed quantity delta); assets are *assigned* and come
back (a lifecycle transition). So there is no ``delta`` here and no
``quantity_on_hand`` — the ledger records which transition happened, and the
current status is the resulting state of the latest row, rebuildable from the
log by :meth:`rebuild_statuses`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from bench_parts.vocabulary import (
    AssetAction,
    AssetStatus,
    AssetType,
    to_asset_action,
    to_asset_status,
    to_asset_type,
)


class _Missing:
    """Sentinel distinguishing "not mentioned" from "set to None" in
    :meth:`AssetStore.update_asset` — the same device parts' store uses."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<missing>"


_MISSING = _Missing()


#: What each ledger action leaves the asset in. This map is what makes the
#: cached status derivable from the log: the current status is the resulting
#: status of the asset's most recent ledger row.
_RESULTING_STATUS: dict[AssetAction, AssetStatus] = {
    AssetAction.ACQUIRE: AssetStatus.AVAILABLE,
    AssetAction.ASSIGN: AssetStatus.ASSIGNED,
    AssetAction.RETURN: AssetStatus.AVAILABLE,
    AssetAction.REPAIR_OUT: AssetStatus.OUT_FOR_REPAIR,
    AssetAction.REPAIR_IN: AssetStatus.AVAILABLE,
    AssetAction.RETIRE: AssetStatus.RETIRED,
    AssetAction.UNRETIRE: AssetStatus.AVAILABLE,
}


@dataclass(frozen=True)
class Asset:
    """A row of the asset registry — one serialized unit of durable test gear."""

    id: int
    name: str
    asset_type: AssetType
    manufacturer: str | None
    model: str | None
    serial: str | None
    location: str | None
    notes: str | None
    status: AssetStatus
    assigned_ref: str | None
    created_at: str
    updated_at: str

    @property
    def available(self) -> bool:
        return self.status is AssetStatus.AVAILABLE

    @property
    def assigned(self) -> bool:
        return self.status is AssetStatus.ASSIGNED

    @property
    def out_for_repair(self) -> bool:
        return self.status is AssetStatus.OUT_FOR_REPAIR

    @property
    def retired(self) -> bool:
        return self.status is AssetStatus.RETIRED


@dataclass(frozen=True)
class AssetMovement:
    """One entry in the append-only asset ledger — a lifecycle transition."""

    id: int
    asset_id: int
    action: AssetAction
    engagement_ref: str | None
    note: str | None
    created_at: str


@dataclass(frozen=True)
class AssetActivity:
    """A ledger row together with the asset it belongs to — what a "what moved"
    view needs in one row, mirroring :class:`bench_parts.store.Activity`."""

    movement: AssetMovement
    asset: Asset


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean(value: object) -> str | None:
    """Normalise optional text: blank-ish becomes ``None``, the rest is stripped."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_text(value: object, field: str) -> str:
    text = _clean(value)
    if text is None:
        raise ValueError(f"{field} is required")
    return text


class AssetStore:
    """Asset registry reads and writes over one connection.

    One store per connection, one connection per request — see
    :func:`bench_parts.db.connect` for why that matters.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --- creation -----------------------------------------------------------

    def create_asset(
        self,
        *,
        name: str,
        asset_type: object,
        manufacturer: object = None,
        model: object = None,
        serial: object = None,
        location: object = None,
        notes: object = None,
        now: str | None = None,
    ) -> Asset:
        """Register a unit of gear. It starts :attr:`AssetStatus.AVAILABLE`.

        An ``acquire`` ledger row is written in the same transaction as the row
        itself, so the log accounts for the asset from the moment it exists —
        no status is ever set without a ledger row behind it. Validation runs
        before any write, so a rejected create leaves nothing behind.
        """
        name = _require_text(name, "name")
        asset_type = to_asset_type(asset_type)
        stamp = now or _now()

        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO assets (name, asset_type, manufacturer, model, serial,"
                " location, notes, status, assigned_ref, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    asset_type.value,
                    _clean(manufacturer),
                    _clean(model),
                    _clean(serial),
                    _clean(location),
                    _clean(notes),
                    AssetStatus.AVAILABLE.value,
                    None,
                    stamp,
                    stamp,
                ),
            )
            asset_id = cursor.lastrowid
            self._conn.execute(
                "INSERT INTO asset_movements (asset_id, action, engagement_ref, note,"
                " created_at) VALUES (?, ?, ?, ?, ?)",
                (asset_id, AssetAction.ACQUIRE.value, None, None, stamp),
            )

        fetched = self.get_asset(asset_id)
        assert fetched is not None  # just written in a transaction
        return fetched

    # --- reading ------------------------------------------------------------

    def get_asset(self, asset_id: int) -> Asset | None:
        row = self._conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return _to_asset(row) if row else None

    def _require(self, asset_id: int) -> Asset:
        asset = self.get_asset(asset_id)
        if asset is None:
            raise LookupError(f"no asset with id {asset_id}")
        return asset

    def list_assets(
        self,
        *,
        asset_type: object = None,
        assigned_ref: object = None,
        status: object = None,
        include_retired: bool = False,
    ) -> list[Asset]:
        """The registry, optionally narrowed.

        ``asset_type`` and ``status`` take stored slugs (any case); an unknown
        one raises rather than returning an empty list — a typo'd filter that
        silently shows nothing is worse than an error. ``assigned_ref`` is the
        filter benchhud's interface monitor uses: the gear out on one
        engagement. Retired assets are excluded unless asked for (or unless
        ``status=retired`` is the explicit filter).
        """
        clauses: list[str] = []
        params: list[object] = []

        if asset_type is not None:
            clauses.append("asset_type = ?")
            params.append(to_asset_type(asset_type).value)
        if status is not None:
            status_value = to_asset_status(status).value
            clauses.append("status = ?")
            params.append(status_value)
        elif not include_retired:
            clauses.append("status != ?")
            params.append(AssetStatus.RETIRED.value)

        ref = _clean(assigned_ref)
        if ref is not None:
            # Verbatim match on the stored ref; case-insensitive for the same
            # reason movements refs are read by a person off a screen.
            clauses.append("assigned_ref = ? COLLATE NOCASE")
            params.append(ref)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            # The WHERE text is assembled from fixed fragments; every user value is bound.
            f"SELECT * FROM assets{where} ORDER BY id",
            tuple(params),
        ).fetchall()
        return [_to_asset(row) for row in rows]

    def list_asset_movements(self, asset_id: int) -> list[AssetMovement]:
        """This asset's ledger, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM asset_movements WHERE asset_id = ? ORDER BY id", (asset_id,)
        ).fetchall()
        return [_to_asset_movement(row) for row in rows]

    def list_recent_asset_movements(
        self, *, limit: int = 50, engagement_ref: object = None
    ) -> list[AssetActivity]:
        """The asset ledger across all assets, newest first, each with its asset.

        ``engagement_ref`` narrows to the assign/return history of one
        engagement. Includes retired assets — retiring hides an asset from the
        registry, not from what happened to it.
        """
        ref = _clean(engagement_ref)
        where = "WHERE m.engagement_ref = ? COLLATE NOCASE" if ref else ""
        params: tuple = (ref, int(limit)) if ref else (int(limit),)
        rows = self._conn.execute(
            "SELECT m.id AS m_id, m.asset_id, m.action, m.engagement_ref, m.note,"
            " m.created_at AS m_created, a.*"
            " FROM asset_movements m JOIN assets a ON a.id = m.asset_id"
            f" {where} ORDER BY m.id DESC LIMIT ?",
            params,
        ).fetchall()
        out = []
        for r in rows:
            movement = AssetMovement(
                id=r["m_id"],
                asset_id=r["asset_id"],
                action=to_asset_action(r["action"]),
                engagement_ref=r["engagement_ref"],
                note=r["note"],
                created_at=r["m_created"],
            )
            out.append(AssetActivity(movement=movement, asset=_to_asset(r)))
        return out

    def list_engagement_refs(self) -> list[str]:
        """Every engagement ref an asset is currently assigned to — distinct,
        sorted. Feeds the UI filter and datalist."""
        rows = self._conn.execute(
            "SELECT DISTINCT assigned_ref FROM assets"
            " WHERE assigned_ref IS NOT NULL ORDER BY assigned_ref COLLATE NOCASE"
        ).fetchall()
        return [row[0] for row in rows]

    # --- lifecycle ----------------------------------------------------------

    def _transition(
        self,
        asset_id: int,
        action: AssetAction,
        *,
        engagement_ref: str | None,
        note: object,
        now: str | None,
    ) -> Asset:
        """Append a ledger row and move the cached status, atomically.

        **Both writes happen in one transaction.** If either fails neither
        lands — a cached status that has drifted from its ledger is the one
        failure this design exists to prevent, and it is silent when it
        happens. The caller is responsible for checking the transition is legal
        (and for a readable message); this method only records a decided move.
        """
        new_status = _RESULTING_STATUS[action]
        new_ref = engagement_ref if action is AssetAction.ASSIGN else None
        stamp = now or _now()

        with self._conn:
            self._conn.execute(
                "INSERT INTO asset_movements (asset_id, action, engagement_ref, note,"
                " created_at) VALUES (?, ?, ?, ?, ?)",
                (asset_id, action.value, engagement_ref, _clean(note), stamp),
            )
            self._conn.execute(
                "UPDATE assets SET status = ?, assigned_ref = ?, updated_at = ? WHERE id = ?",
                (new_status.value, new_ref, stamp, asset_id),
            )

        return self._require(asset_id)

    def assign(
        self, asset_id: int, engagement_ref: str, *, note: object = None, now: str | None = None
    ) -> Asset:
        """Send an available asset out on an engagement.

        Only an :attr:`~AssetStatus.AVAILABLE` asset can be assigned — reassign
        is a mistake worth catching (return it first), and assigning a retired
        or out-for-repair unit is nonsense. ``engagement_ref`` is stored
        verbatim and never validated against bench-recon.
        """
        ref = _require_text(engagement_ref, "engagement ref")
        asset = self._require(asset_id)
        if not asset.available:
            raise ValueError(
                f"asset {asset_id} is {asset.status.value}, not available — "
                f"{self._why_not_available(asset)}"
            )
        return self._transition(
            asset_id, AssetAction.ASSIGN, engagement_ref=ref, note=note, now=now
        )

    def return_asset(self, asset_id: int, *, note: object = None, now: str | None = None) -> Asset:
        """Take an assigned asset back. Only a currently-assigned asset can be
        returned."""
        asset = self._require(asset_id)
        if not asset.assigned:
            raise ValueError(
                f"asset {asset_id} is {asset.status.value}, not assigned — nothing to return"
            )
        return self._transition(
            asset_id, AssetAction.RETURN, engagement_ref=None, note=note, now=now
        )

    def send_for_repair(
        self, asset_id: int, *, note: object = None, now: str | None = None
    ) -> Asset:
        """Mark an asset out for repair. Allowed from available or assigned — gear
        breaks on an engagement, and the ledger row is the record that custody
        ended. A retired or already-out unit can't be sent."""
        asset = self._require(asset_id)
        if asset.status not in (AssetStatus.AVAILABLE, AssetStatus.ASSIGNED):
            raise ValueError(
                f"asset {asset_id} is {asset.status.value} — only an available or assigned "
                "asset can be sent for repair"
            )
        return self._transition(
            asset_id, AssetAction.REPAIR_OUT, engagement_ref=None, note=note, now=now
        )

    def return_from_repair(
        self, asset_id: int, *, note: object = None, now: str | None = None
    ) -> Asset:
        """Bring an asset back from repair to available. Only valid for an asset
        currently out for repair."""
        asset = self._require(asset_id)
        if not asset.out_for_repair:
            raise ValueError(f"asset {asset_id} is {asset.status.value}, not out for repair")
        return self._transition(
            asset_id, AssetAction.REPAIR_IN, engagement_ref=None, note=note, now=now
        )

    def retire_asset(self, asset_id: int, *, note: object = None, now: str | None = None) -> Asset:
        """Retire an asset you no longer use. Reversible via :meth:`unretire_asset`
        (the parts retire pattern). Allowed from any non-retired status — gear
        can be retired straight off an engagement — and clears any assignment.
        Idempotent: retiring a retired asset is a no-op that keeps its history."""
        asset = self._require(asset_id)
        if asset.retired:
            return asset
        return self._transition(
            asset_id, AssetAction.RETIRE, engagement_ref=None, note=note, now=now
        )

    def unretire_asset(self, asset_id: int, *, now: str | None = None) -> Asset:
        """Bring a retired asset back into service, as available."""
        asset = self._require(asset_id)
        if not asset.retired:
            return asset
        return self._transition(
            asset_id, AssetAction.UNRETIRE, engagement_ref=None, note=None, now=now
        )

    @staticmethod
    def _why_not_available(asset: Asset) -> str:
        if asset.assigned:
            return f"it's assigned to {asset.assigned_ref} (return it first)"
        if asset.out_for_repair:
            return "it's out for repair (log it back first)"
        return "unretire it first if you're using it again"

    # --- updating -----------------------------------------------------------

    def update_asset(
        self,
        asset_id: int,
        *,
        name: object = _MISSING,
        asset_type: object = _MISSING,
        manufacturer: object = _MISSING,
        model: object = _MISSING,
        serial: object = _MISSING,
        location: object = _MISSING,
        notes: object = _MISSING,
        now: str | None = None,
    ) -> Asset:
        """Edit catalogue fields. **Cannot change status or assignment.**

        Status moves only through the lifecycle methods, which write a ledger
        row — the same "no cache write without a ledger row" rule the parts
        store enforces for quantity. Only the fields passed change.
        """
        if self.get_asset(asset_id) is None:
            raise LookupError(f"no asset with id {asset_id}")

        updates: dict[str, object] = {}
        if name is not _MISSING:
            updates["name"] = _require_text(name, "name")
        if asset_type is not _MISSING:
            updates["asset_type"] = to_asset_type(asset_type).value
        for field, value in (
            ("manufacturer", manufacturer),
            ("model", model),
            ("serial", serial),
            ("location", location),
            ("notes", notes),
        ):
            if value is not _MISSING:
                updates[field] = _clean(value)

        if updates:
            updates["updated_at"] = now or _now()
            assignments = ", ".join(f"{field} = ?" for field in updates)
            with self._conn:
                self._conn.execute(
                    f"UPDATE assets SET {assignments} WHERE id = ?",
                    (*updates.values(), asset_id),
                )

        return self._require(asset_id)

    # --- deleting and rebuilding -------------------------------------------

    def delete_asset(self, asset_id: int) -> None:
        """Hard-delete a mis-add — **only** if it was never assigned.

        An ``assign`` row is engagement custody history someone may depend on,
        so once one exists this refuses and points at :meth:`retire_asset`
        instead (retiring doesn't launder it). The asset's own setup rows
        (``acquire``, repair) cascade away with it.
        """
        self._require(asset_id)
        assigned = self._conn.execute(
            "SELECT COUNT(*) FROM asset_movements WHERE asset_id = ? AND action = ?",
            (asset_id, AssetAction.ASSIGN.value),
        ).fetchone()[0]
        if assigned:
            raise ValueError(
                f"asset {asset_id} has {assigned} assignment(s) — that's engagement history; "
                "retire it instead of deleting"
            )
        with self._conn:
            # FK ON DELETE CASCADE removes the acquire/repair rows.
            self._conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))

    def rebuild_statuses(self, *, now: str | None = None) -> dict[int, tuple[str, str]]:
        """Recompute every cached status from the ledger — the asset analogue of
        :meth:`bench_parts.store.PartStore.rebuild_quantities`.

        The current status of an asset is the resulting state of its latest
        ledger row; ``assigned_ref`` is that row's engagement ref when it is an
        ``assign``, else NULL. Returns ``{asset_id: (was, now)}`` for the assets
        it changed, so a repair nobody can see doesn't go unlogged.
        """
        # The most recent ledger row per asset: the one with the largest id.
        rows = self._conn.execute(
            "SELECT a.id AS id, a.status AS cached_status, a.assigned_ref AS cached_ref,"
            " m.action AS action, m.engagement_ref AS ref"
            " FROM assets a"
            " LEFT JOIN asset_movements m ON m.id = ("
            "   SELECT MAX(id) FROM asset_movements WHERE asset_id = a.id"
            " ) ORDER BY a.id"
        ).fetchall()

        changed: dict[int, tuple[str, str]] = {}
        fixes: list[tuple[str, str | None, int]] = []
        for row in rows:
            if row["action"] is None:
                # No ledger at all — default to available (shouldn't happen, as
                # create writes an acquire row, but the rebuild stays defined).
                want_status, want_ref = AssetStatus.AVAILABLE, None
            else:
                action = to_asset_action(row["action"])
                want_status = _RESULTING_STATUS[action]
                want_ref = row["ref"] if action is AssetAction.ASSIGN else None
            if row["cached_status"] != want_status.value or row["cached_ref"] != want_ref:
                changed[row["id"]] = (row["cached_status"], want_status.value)
                fixes.append((want_status.value, want_ref, row["id"]))

        if fixes:
            stamp = now or _now()
            with self._conn:
                for status_value, ref, asset_id in fixes:
                    self._conn.execute(
                        "UPDATE assets SET status = ?, assigned_ref = ?, updated_at = ?"
                        " WHERE id = ?",
                        (status_value, ref, stamp, asset_id),
                    )

        return changed


def _to_asset(row: sqlite3.Row) -> Asset:
    return Asset(
        id=row["id"],
        name=row["name"],
        asset_type=AssetType(row["asset_type"]),
        manufacturer=row["manufacturer"],
        model=row["model"],
        serial=row["serial"],
        location=row["location"],
        notes=row["notes"],
        status=AssetStatus(row["status"]),
        assigned_ref=row["assigned_ref"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _to_asset_movement(row: sqlite3.Row) -> AssetMovement:
    return AssetMovement(
        id=row["id"],
        asset_id=row["asset_id"],
        action=to_asset_action(row["action"]),
        engagement_ref=row["engagement_ref"],
        note=row["note"],
        created_at=row["created_at"],
    )
