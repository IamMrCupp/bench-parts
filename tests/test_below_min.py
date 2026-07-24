"""The "to order" query.

Phase 2's view and Phase 4's `?below_min=1` both render this, so its definition
comes from `bench_parts.stock` rather than a second inline comparison. The
cross-check test below is the one that keeps those two honest.
"""

import pytest

from bench_parts.db import connect
from bench_parts.schema import initialise
from bench_parts.stock import StockStatus, is_below_min
from bench_parts.store import PartStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    with connect(tmp_path / "parts.db") as conn:
        initialise(conn)
        yield PartStore(conn)


def _part(store: PartStore, mpn: str, quantity: int, minimum: int):
    return store.create_part(
        description=f"part {mpn}",
        category="mosfet",
        mpn=mpn,
        quantity_on_hand=quantity,
        min_quantity=minimum,
    )


def test_includes_a_part_below_min(store) -> None:
    _part(store, "LOW", quantity=1, minimum=5)

    assert [p.mpn for p in store.list_below_min()] == ["LOW"]


def test_includes_a_part_exactly_at_min(store) -> None:
    """The boundary that decides whether it makes the supplier run."""
    _part(store, "AT-MIN", quantity=5, minimum=5)

    assert [p.mpn for p in store.list_below_min()] == ["AT-MIN"]


def test_excludes_a_part_one_above_min(store) -> None:
    _part(store, "FINE", quantity=6, minimum=5)

    assert store.list_below_min() == []


def test_includes_a_part_at_zero(store) -> None:
    _part(store, "OUT", quantity=0, minimum=2)

    assert [p.mpn for p in store.list_below_min()] == ["OUT"]


def test_excludes_a_stocked_part_with_no_threshold(store) -> None:
    """`min_quantity = 0` reads as "don't flag this one"."""
    _part(store, "NO-THRESHOLD", quantity=3, minimum=0)

    assert store.list_below_min() == []


def test_includes_a_zero_stock_part_with_no_threshold(store) -> None:
    """Out is out, whether or not you asked to be warned."""
    _part(store, "GONE", quantity=0, minimum=0)

    assert [p.mpn for p in store.list_below_min()] == ["GONE"]


def test_orders_out_before_low(store) -> None:
    """Most urgent first — you read this list while placing an order."""
    _part(store, "LOW", quantity=4, minimum=5)
    _part(store, "OUT", quantity=0, minimum=1)

    assert [p.mpn for p in store.list_below_min()] == ["OUT", "LOW"]


def test_orders_by_shortfall_within_low(store) -> None:
    """Further under its threshold means more urgent."""
    _part(store, "BARELY", quantity=5, minimum=5)  # 0 under
    _part(store, "DEEP", quantity=1, minimum=10)  # 9 under

    assert [p.mpn for p in store.list_below_min()] == ["DEEP", "BARELY"]


def test_empty_when_everything_is_stocked(store) -> None:
    _part(store, "A", quantity=10, minimum=2)
    _part(store, "B", quantity=3, minimum=1)

    assert store.list_below_min() == []


def test_reflects_consumption_immediately(store) -> None:
    """The list is a view of current stock, not a snapshot to refresh."""
    part = _part(store, "IRF7910", quantity=6, minimum=5)
    assert store.list_below_min() == []

    store.consume(part.id, 1, ticket_ref="26-0001")

    assert [p.mpn for p in store.list_below_min()] == ["IRF7910"]


def test_query_agrees_with_the_status_predicate(store) -> None:
    """The SQL and `bench_parts.stock` must never disagree.

    If they drift, a part shows amber in the catalogue but never appears on the
    list you actually shop from — the failure mode you'd trust the wrong way
    round at a bench.
    """
    expected = set()
    for quantity in range(0, 6):
        for minimum in range(0, 6):
            mpn = f"Q{quantity}M{minimum}"
            _part(store, mpn, quantity=quantity, minimum=minimum)
            if is_below_min(quantity, minimum):
                expected.add(mpn)

    assert {p.mpn for p in store.list_below_min()} == expected


def test_every_returned_part_is_out_or_low(store) -> None:
    for quantity in range(0, 4):
        for minimum in range(0, 4):
            _part(store, f"Q{quantity}M{minimum}", quantity=quantity, minimum=minimum)

    for part in store.list_below_min():
        assert part.status in (StockStatus.OUT, StockStatus.LOW)
