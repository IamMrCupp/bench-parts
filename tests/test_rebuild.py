"""Rebuilding the cached quantity from the movements log.

This is what turns "the log is the source of truth" from a comment in the
schema into a testable claim. If the cache can always be reconstructed, then a
drift is recoverable rather than a silent, permanent corruption — and it's the
thing you want at 11pm when a count looks wrong.

Every test here corrupts the cache *deliberately, over raw SQL*, because the
store deliberately offers no way to do it.
"""

import pytest

from bench_parts.db import connect
from bench_parts.schema import initialise
from bench_parts.store import PartStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    with connect(tmp_path / "parts.db") as conn:
        initialise(conn)
        yield PartStore(conn)


def _part(store: PartStore, **overrides):
    values = {"description": "Dual N-channel MOSFET", "category": "mosfet"}
    values.update(overrides)
    return store.create_part(**values)


def _corrupt(store: PartStore, part_id: int, wrong_value: int) -> None:
    """Wreck the cache behind the store's back."""
    with store._conn:
        store._conn.execute(
            "UPDATE parts SET quantity_on_hand = ? WHERE id = ?", (wrong_value, part_id)
        )


def test_rebuild_repairs_a_corrupted_cache(store) -> None:
    part = _part(store, quantity_on_hand=10)
    store.consume(part.id, 3)
    _corrupt(store, part.id, 999)

    store.rebuild_quantities()

    assert store.get_part(part.id).quantity_on_hand == 7


def test_rebuild_is_a_no_op_when_nothing_drifted(store) -> None:
    part = _part(store, quantity_on_hand=10)
    store.consume(part.id, 4)

    store.rebuild_quantities()

    assert store.get_part(part.id).quantity_on_hand == 6


def test_rebuild_reports_what_it_changed(store) -> None:
    """A silent repair is nearly useless — you want to know it happened."""
    good = _part(store, quantity_on_hand=5)
    bad = _part(store, quantity_on_hand=5)
    _corrupt(store, bad.id, 42)

    repaired = store.rebuild_quantities()

    assert repaired == {bad.id: (42, 5)}
    assert good.id not in repaired


def test_rebuild_handles_a_part_with_no_movements(store) -> None:
    """A part created with no opening stock has an empty ledger; SUM over no
    rows is NULL, which must read as 0 rather than blowing up."""
    part = _part(store, quantity_on_hand=0)
    _corrupt(store, part.id, 7)

    store.rebuild_quantities()

    assert store.get_part(part.id).quantity_on_hand == 0


def test_rebuild_covers_every_part(store) -> None:
    first = _part(store, quantity_on_hand=3)
    second = _part(store, quantity_on_hand=8)
    _corrupt(store, first.id, 100)
    _corrupt(store, second.id, 200)

    store.rebuild_quantities()

    assert store.get_part(first.id).quantity_on_hand == 3
    assert store.get_part(second.id).quantity_on_hand == 8


def test_rebuild_matches_the_log_after_a_long_sequence(store) -> None:
    part = _part(store, quantity_on_hand=10)
    store.restock(part.id, 5)
    store.consume(part.id, 3, ticket_ref="26-0001")
    store.correct(part.id, -2)
    store.consume(part.id, 1, ticket_ref="26-0002")
    _corrupt(store, part.id, 0)

    store.rebuild_quantities()

    expected = sum(m.delta for m in store.list_movements(part.id))
    assert store.get_part(part.id).quantity_on_hand == expected == 9


def test_rebuild_refuses_to_write_a_negative_quantity(store) -> None:
    """If the log itself sums negative, something is deeply wrong upstream.

    Better to fail loudly than to quietly persist an impossible stock level —
    the schema CHECK would reject it anyway, and swallowing that would hide the
    real problem.
    """
    part = _part(store, quantity_on_hand=5)
    with store._conn:
        store._conn.execute(
            "INSERT INTO movements (part_id, delta, reason, created_at)"
            " VALUES (?, ?, 'correction', 'now')",
            (part.id, -99),
        )

    with pytest.raises(ValueError):
        store.rebuild_quantities()
