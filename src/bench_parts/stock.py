"""Stock status, derived from the counts.

**Never stored.** A part's status is a function of ``quantity_on_hand`` and
``min_quantity``, so persisting it would create a second source of truth that
can disagree with the numbers it came from.

This module is the single definition. Phase 2's colour-coding and the "to order"
query (C.4) both read it rather than re-comparing the two columns themselves —
if those ever drifted, a part could show amber on the catalogue while never
appearing on the list you actually shop from, which is the kind of bug you trust
the wrong way round at a bench.
"""

from __future__ import annotations

from enum import StrEnum


class StockStatus(StrEnum):
    """How a part is doing against its own reorder threshold."""

    OUT = "out"
    LOW = "low"
    OK = "ok"

    @property
    def label(self) -> str:
        """Display text for the UI."""
        return _LABELS[self]


_LABELS: dict[StockStatus, str] = {
    StockStatus.OUT: "Out",
    StockStatus.LOW: "Low",
    StockStatus.OK: "OK",
}

#: Statuses that put a part on the "to order" list.
#:
#: Derived as "everything that isn't OK" rather than hand-listed, mirroring
#: intake's ``OPEN_STATUSES``. A status added later therefore defaults to
#: *appearing* on the order list — the safe direction, since a part nobody
#: classified looks exactly like a part that was lost.
ORDERABLE: frozenset[StockStatus] = frozenset(s for s in StockStatus if s is not StockStatus.OK)


def stock_status(quantity: int, min_quantity: int) -> StockStatus:
    """Classify a part's stock level.

    ``out`` at zero; ``low`` at or below the threshold; ``ok`` above it.

    "At or below" is deliberate: a part sitting exactly on its minimum is one
    job away from being out, so it belongs on the list while you're already
    placing an order.

    A ``min_quantity`` of 0 means "don't flag this one" — such a part is never
    ``low``, only ``out`` when it actually hits zero.
    """
    if quantity < 0:
        raise ValueError(f"quantity cannot be negative, got {quantity}")
    if min_quantity < 0:
        raise ValueError(f"min_quantity cannot be negative, got {min_quantity}")

    if quantity == 0:
        return StockStatus.OUT
    if quantity <= min_quantity:
        return StockStatus.LOW
    return StockStatus.OK


def is_below_min(quantity: int, min_quantity: int) -> bool:
    """Whether this part belongs on the "to order" list.

    Defined in terms of :func:`stock_status` rather than re-comparing the
    columns, so the predicate and the displayed status cannot disagree.
    """
    return stock_status(quantity, min_quantity) in ORDERABLE
