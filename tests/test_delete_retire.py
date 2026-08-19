"""Deleting and retiring parts — the store rules.

Two operations because they solve different problems. `delete_part` is for a
mis-add nothing has been used from; it refuses once there's consumption history
anyone might depend on. `retire_part` is for a real part you no longer stock;
it hides the part but keeps every ledger row, so "what did 26-0001 cost" is
still answerable next year.
"""

import sqlite3

import pytest

from bench_parts.db import connect
from bench_parts.schema import SCHEMA_VERSION, initialise
from bench_parts.store import PartStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    with connect(tmp_path / "parts.db") as conn:
        initialise(conn)
        yield PartStore(conn)


def _part(store, **kw):
    values = {"description": "Dual N-channel MOSFET", "category": "mosfet", "mpn": "IRF7910"}
    values.update(kw)
    return store.create_part(**values)


def test_schema_is_at_least_v3() -> None:
    assert SCHEMA_VERSION >= 3


# --- delete -----------------------------------------------------------------


def test_delete_a_part_with_no_history(store) -> None:
    part = _part(store)

    store.delete_part(part.id)

    assert store.get_part(part.id) is None


def test_delete_a_part_with_only_setup_movements(store) -> None:
    """An opening count, a restock, a correction — that's the part's own setup,
    not history anyone else depends on. Deleting is fine and cascades them."""
    part = _part(store, quantity_on_hand=5)
    store.restock(part.id, 3)
    store.correct(part.id, -1)

    store.delete_part(part.id)

    assert store.get_part(part.id) is None
    assert store.list_movements(part.id) == []


def test_delete_refuses_once_something_has_been_consumed(store) -> None:
    """The one that matters: a consumption is job-costing history."""
    part = _part(store, quantity_on_hand=5)
    store.consume(part.id, 1, ticket_ref="26-0001")

    with pytest.raises(ValueError, match="retire"):
        store.delete_part(part.id)

    assert store.get_part(part.id) is not None
    assert len(store.list_movements(part.id)) == 2


def test_delete_refuses_consumption_even_without_a_ticket_ref(store) -> None:
    """Used is used, whether or not it was booked to a job."""
    part = _part(store, quantity_on_hand=5)
    store.consume(part.id, 1)

    with pytest.raises(ValueError):
        store.delete_part(part.id)


def test_delete_unknown_part_raises(store) -> None:
    with pytest.raises(LookupError):
        store.delete_part(9999)


def test_delete_leaves_other_parts_alone(store) -> None:
    keep = _part(store, mpn="KEEP", quantity_on_hand=2)
    gone = _part(store, mpn="GONE")

    store.delete_part(gone.id)

    assert store.get_part(keep.id).quantity_on_hand == 2
    assert len(store.list_movements(keep.id)) == 1


# --- retire -----------------------------------------------------------------


def test_retire_sets_retired_at_and_keeps_the_ledger(store) -> None:
    part = _part(store, quantity_on_hand=5)
    store.consume(part.id, 2, ticket_ref="26-0001")

    retired = store.retire_part(part.id)

    assert retired.retired_at is not None
    assert retired.retired
    assert len(store.list_movements(part.id)) == 2
    assert store.list_movements_by_ticket("26-0001")[0].part_id == part.id


def test_retired_parts_are_hidden_by_default(store) -> None:
    live = _part(store, mpn="LIVE")
    old = _part(store, mpn="OLD")
    store.retire_part(old.id)

    assert [p.mpn for p in store.list_parts()] == ["LIVE"]
    assert [p.mpn for p in store.search(q="OLD")] == []
    assert store.get_part(old.id) is not None  # direct lookup still works
    assert live.id in {p.id for p in store.list_parts()}


def test_retired_parts_stay_off_the_order_list(store) -> None:
    """A part you no longer stock must not nag you to buy it."""
    old = _part(store, mpn="OLD", quantity_on_hand=0, min_quantity=5)
    assert [p.mpn for p in store.list_below_min()] == ["OLD"]

    store.retire_part(old.id)

    assert store.list_below_min() == []


def test_include_retired_brings_them_back(store) -> None:
    old = _part(store, mpn="OLD")
    store.retire_part(old.id)

    assert [p.mpn for p in store.list_parts(include_retired=True)] == ["OLD"]
    assert [p.mpn for p in store.search(q="old", include_retired=True)] == ["OLD"]


def test_unretire_restores(store) -> None:
    old = _part(store, mpn="OLD", quantity_on_hand=0, min_quantity=5)
    store.retire_part(old.id)

    back = store.unretire_part(old.id)

    assert back.retired_at is None
    assert [p.mpn for p in store.list_parts()] == ["OLD"]
    assert [p.mpn for p in store.list_below_min()] == ["OLD"]


def test_retire_is_idempotent(store) -> None:
    part = _part(store)
    first = store.retire_part(part.id).retired_at
    second = store.retire_part(part.id).retired_at

    assert first == second  # doesn't bump the timestamp


def test_retire_unknown_part_raises(store) -> None:
    with pytest.raises(LookupError):
        store.retire_part(9999)


def test_movements_on_a_retired_part_are_refused(store) -> None:
    """Retired means "no longer stocked". Restocking it is a mistake — unretire
    first, deliberately."""
    part = _part(store, quantity_on_hand=5)
    store.retire_part(part.id)

    with pytest.raises(ValueError, match="retired"):
        store.restock(part.id, 1)
    with pytest.raises(ValueError, match="retired"):
        store.consume(part.id, 1)


def test_delete_a_retired_part_follows_the_same_rule(store) -> None:
    """Retiring doesn't launder history — a retired part with consumption still
    can't be hard-deleted."""
    part = _part(store, quantity_on_hand=5)
    store.consume(part.id, 1, ticket_ref="26-0001")
    store.retire_part(part.id)

    with pytest.raises(ValueError):
        store.delete_part(part.id)


# --- migration --------------------------------------------------------------


def test_v2_database_upgrades_to_v3(tmp_path, monkeypatch) -> None:
    """A v2 database (no retired_at column) gains it in place, rows intact."""
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    path = tmp_path / "parts.db"
    with connect(path) as conn:
        initialise(conn)
        PartStore(conn).create_part(description="x", category="mosfet", quantity_on_hand=3)
        # Roll it back to v2 by hand: drop the column and the version.
        conn.execute("ALTER TABLE parts DROP COLUMN retired_at")
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(parts)")}
        assert "retired_at" not in cols

    with connect(path) as conn:
        initialise(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(parts)")}
        assert "retired_at" in cols
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        part = PartStore(conn).get_part(1)
        assert part.quantity_on_hand == 3
        assert part.retired_at is None


def test_v3_migration_is_idempotent(store) -> None:
    initialise(store._conn)
    initialise(store._conn)

    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    with pytest.raises(sqlite3.OperationalError):
        # Proves the column exists exactly once — a second ADD would fail.
        store._conn.execute("ALTER TABLE parts ADD COLUMN retired_at TEXT")
