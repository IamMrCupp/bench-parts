"""Schema initialisation and the constraints the database enforces itself.

The CHECK constraints are tested directly against SQL inserts rather than
through the store API on purpose. They exist precisely so that a future script,
a manual `sqlite3` session, or a bug in the Phase 4 API can't route around them
— so the tests have to route around the API too, or they'd be testing the wrong
layer.
"""

import sqlite3

import pytest

from bench_parts.db import connect
from bench_parts.schema import SCHEMA_VERSION, initialise
from bench_parts.vocabulary import Category


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    with connect(tmp_path / "parts.db") as c:
        initialise(c)
        yield c


def _insert_part(conn: sqlite3.Connection, **overrides) -> int:
    """Insert a part straight over SQL, bypassing any store logic."""
    row = {
        "mpn": "IRF7910",
        "manufacturer": "Infineon",
        "description": "Dual N-channel MOSFET",
        "category": "mosfet",
        "quantity_on_hand": 5,
        "min_quantity": 2,
        "unit_cost": 187,
        "supplier": "Digi-Key",
        "supplier_url": None,
        "location": "A3",
        "notes": None,
        "created_at": "2026-07-24T00:00:00Z",
        "updated_at": "2026-07-24T00:00:00Z",
    }
    row.update(overrides)
    cols = ", ".join(row)
    marks = ", ".join(["?"] * len(row))
    cur = conn.execute(f"INSERT INTO parts ({cols}) VALUES ({marks})", tuple(row.values()))
    return cur.lastrowid


def _insert_movement(conn: sqlite3.Connection, part_id: int, **overrides) -> None:
    row = {
        "part_id": part_id,
        "delta": -1,
        "reason": "consume",
        "ticket_ref": "26-0001",
        "unit_cost_at_time": 187,
        "created_at": "2026-07-24T00:00:00Z",
    }
    row.update(overrides)
    cols = ", ".join(row)
    marks = ", ".join(["?"] * len(row))
    conn.execute(f"INSERT INTO movements ({cols}) VALUES ({marks})", tuple(row.values()))


# --- creation and migration -------------------------------------------------


@pytest.mark.parametrize("table", ["parts", "movements"])
def test_initialise_creates_tables(conn, table) -> None:
    found = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()

    assert found is not None


def test_initialise_sets_user_version(conn) -> None:
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_initialise_is_idempotent(conn) -> None:
    """Running it twice is a no-op, not an error — and keeps existing rows."""
    _insert_part(conn)
    conn.commit()

    initialise(conn)
    initialise(conn)

    assert conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


@pytest.mark.parametrize("index", ["idx_parts_mpn", "idx_parts_category", "idx_movements_part"])
def test_indexes_exist(conn, index) -> None:
    found = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?", (index,)
    ).fetchone()

    assert found is not None


# --- parts constraints ------------------------------------------------------


def test_every_category_is_accepted(conn) -> None:
    """The CHECK is generated from the enum, so all members must pass."""
    for i, category in enumerate(Category):
        _insert_part(conn, category=category.value, mpn=f"MPN-{i}")

    assert conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == len(list(Category))


def test_unknown_category_is_rejected(conn) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _insert_part(conn, category="mosfets")


def test_negative_quantity_is_rejected(conn) -> None:
    """The backstop behind the store's consume-more-than-you-have guard."""
    with pytest.raises(sqlite3.IntegrityError):
        _insert_part(conn, quantity_on_hand=-1)


def test_negative_min_quantity_is_rejected(conn) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _insert_part(conn, min_quantity=-1)


def test_negative_unit_cost_is_rejected(conn) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _insert_part(conn, unit_cost=-1)


def test_null_unit_cost_is_allowed(conn) -> None:
    """Cost is optional — plenty of salvaged parts have no meaningful price."""
    _insert_part(conn, unit_cost=None)

    assert conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 1


def test_description_is_required(conn) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _insert_part(conn, description=None)


def test_mpn_may_be_null(conn) -> None:
    """Salvaged and generic parts genuinely have no manufacturer part number."""
    _insert_part(conn, mpn=None)

    assert conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 1


def test_mpn_is_deliberately_not_unique(conn) -> None:
    """The same MPN spans manufacturers, and duplicates are a real shop state.

    If this ever starts failing, someone has "tidied" the schema by adding a
    UNIQUE constraint — see the comment in schema.py before doing that.
    """
    _insert_part(conn, mpn="IRF7910", manufacturer="Infineon")
    _insert_part(conn, mpn="IRF7910", manufacturer="Vishay")

    assert conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 2


# --- movements constraints --------------------------------------------------


def test_zero_delta_movement_is_rejected(conn) -> None:
    """A movement that moves nothing is a bug, not a record."""
    part_id = _insert_part(conn)

    with pytest.raises(sqlite3.IntegrityError):
        _insert_movement(conn, part_id, delta=0)


def test_unknown_reason_is_rejected(conn) -> None:
    part_id = _insert_part(conn)

    with pytest.raises(sqlite3.IntegrityError):
        _insert_movement(conn, part_id, reason="shrinkage")


def test_movement_requires_an_existing_part(conn) -> None:
    """Relies on foreign_keys=ON from db.connect — an orphaned movement would
    silently corrupt the stock history."""
    with pytest.raises(sqlite3.IntegrityError):
        _insert_movement(conn, 9999)


def test_deleting_a_part_cascades_to_its_movements(conn) -> None:
    part_id = _insert_part(conn)
    _insert_movement(conn, part_id)
    conn.commit()

    conn.execute("DELETE FROM parts WHERE id = ?", (part_id,))

    assert conn.execute("SELECT COUNT(*) FROM movements").fetchone()[0] == 0


def test_ticket_ref_is_optional(conn) -> None:
    """Restocks and corrections aren't tied to a job."""
    part_id = _insert_part(conn)
    _insert_movement(conn, part_id, reason="restock", delta=10, ticket_ref=None)

    assert conn.execute("SELECT COUNT(*) FROM movements").fetchone()[0] == 1


def test_ticket_ref_is_stored_verbatim(conn) -> None:
    """Never validated against intake — loose coupling across services."""
    part_id = _insert_part(conn)
    _insert_movement(conn, part_id, ticket_ref="not-a-real-ref")

    stored = conn.execute("SELECT ticket_ref FROM movements").fetchone()[0]

    assert stored == "not-a-real-ref"
