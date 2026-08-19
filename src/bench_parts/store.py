"""The parts store.

**The movements log is the source of truth; ``parts.quantity_on_hand`` is a
cache.** The rule that makes that true is enforced here, not in the schema:
*no code path changes a quantity without writing a movement in the same
transaction*. It starts at :meth:`PartStore.create_part` — an opening count is
an ``initial`` movement, not a bare column write — and :meth:`update_part`
refuses to touch ``quantity_on_hand`` at all.

Validation happens in Python so callers get a useful message; the schema's CHECK
constraints are the backstop for anything that routes around this module.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bench_parts.stock import StockStatus, is_below_min, stock_status
from bench_parts.vocabulary import Category, MovementReason, to_category, to_reason


class _Unset:
    """Sentinel distinguishing "not mentioned" from "set to None"."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<unset>"


UNSET = _Unset()


@dataclass(frozen=True)
class Part:
    """A row of the catalogue.

    ``quantity_on_hand`` is the cached sum of this part's movements. ``status``
    is derived on read, never stored — see :mod:`bench_parts.stock`.
    """

    id: int
    mpn: str | None
    manufacturer: str | None
    description: str
    category: Category
    quantity_on_hand: int
    min_quantity: int
    unit_cost: int | None
    supplier: str | None
    supplier_url: str | None
    location: str | None
    notes: str | None
    created_at: str
    updated_at: str
    retired_at: str | None = None
    project: str | None = None

    @property
    def retired(self) -> bool:
        return self.retired_at is not None

    @property
    def status(self) -> StockStatus:
        """Out / low / ok, derived from the two counts."""
        return stock_status(self.quantity_on_hand, self.min_quantity)


@dataclass(frozen=True)
class Movement:
    """One entry in the append-only stock ledger."""

    id: int
    part_id: int
    delta: int
    reason: MovementReason
    ticket_ref: str | None
    unit_cost_at_time: int | None
    created_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean(value: object) -> str | None:
    """Normalise optional text: blank-ish becomes ``None``, the rest is stripped.

    A form posts ``''`` where a script posts ``None``; neither should produce a
    row with a whitespace-only location that reads as set but isn't.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _escape_like(text: str) -> str:
    """Escape SQL LIKE wildcards in user input so "100%" searches for a percent
    sign rather than matching everything. Pairs with ``ESCAPE '\\'`` — see search()."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _require_text(value: object, field: str) -> str:
    text = _clean(value)
    if text is None:
        raise ValueError(f"{field} is required")
    return text


def _require_non_negative(value: object, field: str) -> int:
    number = int(value)
    if number < 0:
        raise ValueError(f"{field} cannot be negative, got {number}")
    return number


def _optional_non_negative(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _require_non_negative(value, field)


class PartStore:
    """Catalogue reads and writes over one connection.

    One store per connection, one connection per request — see
    :func:`bench_parts.db.connect` for why that matters.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --- creation -----------------------------------------------------------

    def create_part(
        self,
        *,
        description: str,
        category: object,
        mpn: object = None,
        manufacturer: object = None,
        quantity_on_hand: int = 0,
        min_quantity: int = 0,
        unit_cost: int | None = None,
        supplier: object = None,
        supplier_url: object = None,
        location: object = None,
        notes: object = None,
        project: object = None,
        now: str | None = None,
    ) -> Part:
        """Add a part to the catalogue.

        An opening ``quantity_on_hand`` is recorded as an ``initial`` movement
        in the same transaction as the row itself, so the log accounts for every
        unit from the first one. Validation runs *before* any write, so a
        rejected create leaves nothing behind.
        """
        description = _require_text(description, "description")
        category = to_category(category)
        quantity_on_hand = _require_non_negative(quantity_on_hand, "quantity_on_hand")
        min_quantity = _require_non_negative(min_quantity, "min_quantity")
        unit_cost = _optional_non_negative(unit_cost, "unit_cost")
        stamp = now or _now()

        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO parts (mpn, manufacturer, description, category,"
                " quantity_on_hand, min_quantity, unit_cost, supplier, supplier_url,"
                " location, notes, project, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _clean(mpn),
                    _clean(manufacturer),
                    description,
                    category.value,
                    quantity_on_hand,
                    min_quantity,
                    unit_cost,
                    _clean(supplier),
                    _clean(supplier_url),
                    _clean(location),
                    _clean(notes),
                    _clean(project),
                    stamp,
                    stamp,
                ),
            )
            part_id = cursor.lastrowid

            # Zero is not a movement — `delta != 0` is a schema constraint, and
            # a row saying "nothing happened" is noise in an audit trail.
            if quantity_on_hand:
                self._conn.execute(
                    "INSERT INTO movements (part_id, delta, reason, ticket_ref,"
                    " unit_cost_at_time, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        part_id,
                        quantity_on_hand,
                        MovementReason.INITIAL.value,
                        None,
                        unit_cost,
                        stamp,
                    ),
                )

        fetched = self.get_part(part_id)
        # Just written inside a transaction — this cannot be None.
        assert fetched is not None
        return fetched

    # --- reading ------------------------------------------------------------

    def get_part(self, part_id: int) -> Part | None:
        row = self._conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
        return _to_part(row) if row else None

    def find_by_mpn(self, mpn: str) -> list[Part]:
        """Every part carrying this MPN.

        Case-insensitive — you're reading a marking through a loupe, not typing
        a search term. Returns a list because MPNs are deliberately not unique:
        the same number legitimately spans manufacturers.
        """
        rows = self._conn.execute(
            "SELECT * FROM parts WHERE mpn = ? COLLATE NOCASE ORDER BY id", (_clean(mpn),)
        ).fetchall()
        return [_to_part(row) for row in rows]

    def list_parts(
        self, category: object = None, *, project: object = None, include_retired: bool = False
    ) -> list[Part]:
        """The catalogue, optionally narrowed to one category.

        Retired parts are excluded unless asked for — they're not stock. An
        unknown category raises rather than returning nothing — a typo'd filter
        that silently shows an empty shelf is worse than an error.
        """
        return self.search(category=category, project=project, include_retired=include_retired)

    def search(
        self,
        q: object = None,
        category: object = None,
        *,
        project: object = None,
        include_retired: bool = False,
    ) -> list[Part]:
        """The catalogue, narrowed by a free-text query and/or a category.

        Retired parts are excluded unless ``include_retired`` — see
        :meth:`retire_part`.

        ``q`` is a case-insensitive **substring** match against MPN and
        description — you remember "IRF" and half a number, not the whole
        thing. Blank ``q`` means no text filter. An unknown ``category`` raises,
        same as :meth:`list_parts`.
        """
        text = _clean(q)
        clauses: list[str] = []
        params: list[object] = []

        if not include_retired:
            clauses.append("retired_at IS NULL")
        proj = _clean(project)
        if proj is not None:
            # Case-insensitive: "audiophore" and "Audiophore" are one project.
            clauses.append("project = ? COLLATE NOCASE")
            params.append(proj)
        if category is not None:
            clauses.append("category = ?")
            params.append(to_category(category).value)
        if text is not None:
            clauses.append(
                "(mpn LIKE ? ESCAPE '\\' COLLATE NOCASE"
                " OR description LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            )
            like = f"%{_escape_like(text)}%"
            params.extend([like, like])

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            # The WHERE text is assembled from fixed fragments; every user value is bound.
            f"SELECT * FROM parts{where} ORDER BY id",
            tuple(params),
        ).fetchall()
        return [_to_part(row) for row in rows]

    def list_below_min(self, *, project: object = None) -> list[Part]:
        """The "to order" list — everything at or below its reorder threshold.

        Filtered through :func:`bench_parts.stock.is_below_min` rather than a
        SQL ``WHERE quantity_on_hand <= min_quantity``. The two are equivalent
        today, and that's exactly the problem: a second expression of the rule
        is one that can drift from the first. Phase 2's colours and Phase 4's
        ``?below_min=1`` both render this, and a list that disagrees with the
        colours is the failure you'd trust the wrong way round at a bench.

        The cost is reading the catalogue to filter it, which is nothing at
        bench scale (hundreds of parts). If this ever grows to where that
        matters, push the predicate into SQL *and* keep the cross-check test
        that pins it against this function.

        Ordered most-urgent-first: out before low, then by how far under.
        """
        # list_parts() already excludes retired parts — a part you no longer
        # stock must not nag you to buy it.
        below = [
            p
            for p in self.list_parts(project=project)
            if is_below_min(p.quantity_on_hand, p.min_quantity)
        ]
        return sorted(
            below,
            key=lambda p: (
                p.status is not StockStatus.OUT,
                -(p.min_quantity - p.quantity_on_hand),
                p.id,
            ),
        )

    def list_projects(self) -> list[str]:
        """Every project name in use by a live part — distinct, case-folded,
        sorted. Feeds the catalogue filter and the form's datalist, so an
        existing name gets picked rather than retyped three ways."""
        rows = self._conn.execute(
            "SELECT project FROM parts WHERE project IS NOT NULL AND retired_at IS NULL"
        ).fetchall()
        seen: dict[str, str] = {}
        for (name,) in rows:
            seen.setdefault(name.casefold(), name)  # first spelling wins
        return sorted(seen.values(), key=str.casefold)

    def list_movements(self, part_id: int) -> list[Movement]:
        """This part's ledger, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM movements WHERE part_id = ? ORDER BY id", (part_id,)
        ).fetchall()
        return [_to_movement(row) for row in rows]

    def list_movements_by_ticket(self, ticket_ref: str) -> list[Movement]:
        """Everything recorded against one job ref, oldest first.

        The costing query — "what parts went into 26-0001, and what did they
        cost at the time." A ref this service has never seen returns an empty
        list, not an error: it's a job that used no parts, and bench-parts
        never validates refs against intake anyway.
        """
        ref = _clean(ticket_ref)
        if ref is None:
            return []
        rows = self._conn.execute(
            "SELECT * FROM movements WHERE ticket_ref = ? ORDER BY id", (ref,)
        ).fetchall()
        return [_to_movement(row) for row in rows]

    # --- movements ----------------------------------------------------------

    def record_movement(
        self,
        part_id: int,
        delta: int,
        reason: object,
        *,
        ticket_ref: object = None,
        unit_cost_at_time: object = UNSET,
        now: str | None = None,
    ) -> Part:
        """Append to the ledger and move the cached count, atomically.

        **Both writes happen in one transaction.** If either fails, neither
        lands — a cache that has drifted from its log is the single failure this
        whole design exists to prevent, and it is silent when it happens.

        The cache update is *relative* (``quantity_on_hand + ?``) and carries its
        own ``>= 0`` guard in the ``WHERE`` clause, rather than computing a new
        total from a value read earlier. That read-then-write gap is where
        concurrent consumers lose updates; SQLite evaluates this in one
        statement, so two threads taking the last unit can't both succeed.

        ``unit_cost_at_time`` defaults to the part's current ``unit_cost``, so a
        later price change can't rewrite what a past job cost.
        """
        reason = to_reason(reason)
        delta = int(delta)
        if delta == 0:
            raise ValueError("a movement must change the quantity — delta cannot be 0")

        part = self.get_part(part_id)
        if part is None:
            raise LookupError(f"no part with id {part_id}")
        if part.retired:
            raise ValueError(
                f"part {part_id} is retired — unretire it first if you're stocking it again"
            )

        # Checked here for a message that names the actual numbers; the WHERE
        # clause below is what actually enforces it under concurrency.
        if part.quantity_on_hand + delta < 0:
            raise ValueError(
                f"cannot remove {-delta} — only {part.quantity_on_hand} on hand for part {part_id}"
            )

        cost = part.unit_cost if isinstance(unit_cost_at_time, _Unset) else unit_cost_at_time
        cost = _optional_non_negative(cost, "unit_cost_at_time")
        stamp = now or _now()

        with self._conn:
            self._conn.execute(
                "INSERT INTO movements (part_id, delta, reason, ticket_ref,"
                " unit_cost_at_time, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (part_id, delta, reason.value, _clean(ticket_ref), cost, stamp),
            )
            cursor = self._conn.execute(
                "UPDATE parts SET quantity_on_hand = quantity_on_hand + ?, updated_at = ?"
                " WHERE id = ? AND quantity_on_hand + ? >= 0",
                (delta, stamp, part_id, delta),
            )
            if cursor.rowcount != 1:
                # Another writer got in between the check above and this update.
                # Raising inside the context manager rolls the movement back too.
                raise ValueError(f"cannot remove {-delta} from part {part_id} — insufficient stock")

        fetched = self.get_part(part_id)
        # Just written inside a transaction — this cannot be None.
        assert fetched is not None
        return fetched

    def rebuild_quantities(self, *, now: str | None = None) -> dict[int, tuple[int, int]]:
        """Recompute every cached quantity from the movements log.

        This is what makes "the log is the source of truth" a claim you can
        check rather than a comment in the schema. A drifted cache becomes a
        recoverable problem instead of silent, permanent corruption — and this
        is the thing you want at 11pm when a count looks wrong.

        Returns ``{part_id: (was, now)}`` for the parts it changed. A repair
        nobody can see is nearly useless; the caller should log this.

        Raises if the log itself sums negative for any part: that means
        something upstream is broken, and persisting an impossible stock level
        would bury the real problem. Validation happens across every part
        *before* any write, so a bad log leaves the cache untouched rather than
        half-rewritten.
        """
        rows = self._conn.execute(
            "SELECT p.id AS id, p.quantity_on_hand AS cached,"
            " COALESCE((SELECT SUM(m.delta) FROM movements m WHERE m.part_id = p.id), 0)"
            " AS from_log"
            " FROM parts p ORDER BY p.id"
        ).fetchall()

        for row in rows:
            if row["from_log"] < 0:
                raise ValueError(
                    f"movements for part {row['id']} sum to {row['from_log']}, which is "
                    "impossible — refusing to rebuild until the log is corrected"
                )

        changed = {
            row["id"]: (row["cached"], row["from_log"])
            for row in rows
            if row["cached"] != row["from_log"]
        }

        if changed:
            stamp = now or _now()
            with self._conn:
                for part_id, (_was, corrected) in changed.items():
                    self._conn.execute(
                        "UPDATE parts SET quantity_on_hand = ?, updated_at = ? WHERE id = ?",
                        (corrected, stamp, part_id),
                    )

        return changed

    def restock(self, part_id: int, amount: int, *, now: str | None = None, **kwargs: Any) -> Part:
        """Add stock. ``amount`` is positive."""
        if int(amount) <= 0:
            raise ValueError(f"restock amount must be positive, got {amount}")
        return self.record_movement(part_id, int(amount), MovementReason.RESTOCK, now=now, **kwargs)

    def consume(
        self,
        part_id: int,
        amount: int,
        *,
        ticket_ref: object = None,
        now: str | None = None,
    ) -> Part:
        """Use stock on a job. ``amount`` is positive and is negated here.

        Taking a positive count rather than a signed delta means a caller can't
        accidentally *add* stock by passing a negative number to "consume".
        """
        if int(amount) <= 0:
            raise ValueError(f"consume amount must be positive, got {amount}")
        return self.record_movement(
            part_id, -int(amount), MovementReason.CONSUME, ticket_ref=ticket_ref, now=now
        )

    def correct(self, part_id: int, delta: int, *, now: str | None = None) -> Part:
        """Fix a miscount. Signed — a correction can go either way.

        A correction is a *new row* with an opposing delta, never an edit of the
        movement that was wrong. History stays append-only, which is what keeps
        the cache rebuildable and past job costs reconstructable.
        """
        return self.record_movement(part_id, delta, MovementReason.CORRECTION, now=now)

    # --- retiring and deleting ---------------------------------------------

    def retire_part(self, part_id: int, *, now: str | None = None) -> Part:
        """Soft-delete: hide a part you no longer stock, keep its history.

        Sets ``retired_at``; the part drops out of the catalogue, search and the
        "to order" list, and refuses further movements. **Every ledger row
        stays**, so "what did 26-0001 cost" is still answerable. Idempotent —
        retiring twice keeps the original timestamp.
        """
        part = self._require(part_id)
        if part.retired:
            return part
        with self._conn:
            self._conn.execute(
                "UPDATE parts SET retired_at = ?, updated_at = ? WHERE id = ?",
                (now or _now(), now or _now(), part_id),
            )
        return self._require(part_id)

    def unretire_part(self, part_id: int, *, now: str | None = None) -> Part:
        """Bring a retired part back into stock."""
        self._require(part_id)
        with self._conn:
            self._conn.execute(
                "UPDATE parts SET retired_at = NULL, updated_at = ? WHERE id = ?",
                (now or _now(), part_id),
            )
        return self._require(part_id)

    def delete_part(self, part_id: int) -> None:
        """Hard-delete a mis-add — **only** if nothing has been used from it.

        The part's own setup rows (``initial``, restock, correction) are its
        business and cascade away with it. A ``consume`` row is different: it's
        job-costing history someone else may depend on, so once one exists this
        refuses and points at :meth:`retire_part` instead. Retiring doesn't
        launder that — a retired part with consumption still can't be deleted.
        """
        self._require(part_id)
        consumed = self._conn.execute(
            "SELECT COUNT(*) FROM movements WHERE part_id = ? AND reason = ?",
            (part_id, MovementReason.CONSUME.value),
        ).fetchone()[0]
        if consumed:
            raise ValueError(
                f"part {part_id} has {consumed} consumption movement(s) — that's job history; "
                "retire it instead of deleting"
            )
        with self._conn:
            # FK ON DELETE CASCADE removes the setup movements.
            self._conn.execute("DELETE FROM parts WHERE id = ?", (part_id,))

    def _require(self, part_id: int) -> Part:
        part = self.get_part(part_id)
        if part is None:
            raise LookupError(f"no part with id {part_id}")
        return part

    # --- updating -----------------------------------------------------------

    def update_part(
        self,
        part_id: int,
        *,
        mpn: object = UNSET,
        manufacturer: object = UNSET,
        description: object = UNSET,
        category: object = UNSET,
        min_quantity: object = UNSET,
        unit_cost: object = UNSET,
        supplier: object = UNSET,
        supplier_url: object = UNSET,
        location: object = UNSET,
        notes: object = UNSET,
        project: object = UNSET,
        quantity_on_hand: object = UNSET,
        now: str | None = None,
    ) -> Part:
        """Edit catalogue fields. **Cannot change stock.**

        ``quantity_on_hand`` is rejected outright: allowing it here would create
        a second path that changes stock without writing a movement, which is
        exactly the drift the movements log exists to prevent. Use
        ``restock`` / ``consume`` / ``correct`` instead.

        ``min_quantity`` *is* editable — a reorder threshold is policy, not a
        stock level.
        """
        if not isinstance(quantity_on_hand, _Unset):
            raise ValueError(
                "quantity_on_hand cannot be set directly — record a movement "
                "(restock / consume / correct) so the log stays authoritative"
            )

        if self.get_part(part_id) is None:
            raise LookupError(f"no part with id {part_id}")

        updates: dict[str, Any] = {}

        if not isinstance(description, _Unset):
            updates["description"] = _require_text(description, "description")
        if not isinstance(category, _Unset):
            updates["category"] = to_category(category).value
        if not isinstance(min_quantity, _Unset):
            updates["min_quantity"] = _require_non_negative(min_quantity, "min_quantity")
        if not isinstance(unit_cost, _Unset):
            updates["unit_cost"] = _optional_non_negative(unit_cost, "unit_cost")

        for field, value in (
            ("mpn", mpn),
            ("manufacturer", manufacturer),
            ("supplier", supplier),
            ("supplier_url", supplier_url),
            ("location", location),
            ("notes", notes),
            ("project", project),
        ):
            if not isinstance(value, _Unset):
                updates[field] = _clean(value)

        if updates:
            updates["updated_at"] = now or _now()
            assignments = ", ".join(f"{field} = ?" for field in updates)
            with self._conn:
                self._conn.execute(
                    f"UPDATE parts SET {assignments} WHERE id = ?",
                    (*updates.values(), part_id),
                )

        fetched = self.get_part(part_id)
        # Existence was checked above and nothing deletes in between.
        assert fetched is not None
        return fetched


def _to_part(row: sqlite3.Row) -> Part:
    return Part(
        id=row["id"],
        mpn=row["mpn"],
        manufacturer=row["manufacturer"],
        description=row["description"],
        category=Category(row["category"]),
        quantity_on_hand=row["quantity_on_hand"],
        min_quantity=row["min_quantity"],
        unit_cost=row["unit_cost"],
        supplier=row["supplier"],
        supplier_url=row["supplier_url"],
        location=row["location"],
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        retired_at=row["retired_at"],
        project=row["project"],
    )


def _to_movement(row: sqlite3.Row) -> Movement:
    return Movement(
        id=row["id"],
        part_id=row["part_id"],
        delta=row["delta"],
        reason=to_reason(row["reason"]),
        ticket_ref=row["ticket_ref"],
        unit_cost_at_time=row["unit_cost_at_time"],
        created_at=row["created_at"],
    )
