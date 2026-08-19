"""Schema v2 — Filament and Module categories, and the first real migration.

The category CHECK is generated from the enum at table creation, and SQLite
can't ALTER a CHECK, so extending the enum means rebuilding the `parts` table.
That's the path under test: a v1 database with rows and movements must come out
the other side at v2 with everything intact, and the new categories accepted.
"""

import sqlite3

import pytest

from bench_parts.db import connect
from bench_parts.schema import SCHEMA_VERSION, initialise
from bench_parts.store import PartStore
from bench_parts.vocabulary import Category, to_category

# The v1 DDL, frozen here so the upgrade path is tested against what actually
# shipped rather than against whatever the current schema module says.
_V1_PARTS = """
CREATE TABLE parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, mpn TEXT, manufacturer TEXT,
    description TEXT NOT NULL, category TEXT NOT NULL,
    quantity_on_hand INTEGER NOT NULL DEFAULT 0, min_quantity INTEGER NOT NULL DEFAULT 0,
    unit_cost INTEGER, supplier TEXT, supplier_url TEXT, location TEXT, notes TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    CONSTRAINT known_category CHECK (category IN ('cable_wire', 'capacitor', 'connector',
      'consumable', 'crystal_oscillator', 'diode', 'fuse', 'hardware_mechanical', 'ic',
      'inductor', 'led_opto', 'mosfet', 'other', 'relay', 'resistor', 'switch',
      'transformer', 'transistor_bjt')),
    CONSTRAINT quantity_not_negative CHECK (quantity_on_hand >= 0),
    CONSTRAINT min_quantity_not_negative CHECK (min_quantity >= 0),
    CONSTRAINT unit_cost_not_negative CHECK (unit_cost IS NULL OR unit_cost >= 0)
);
CREATE INDEX idx_parts_mpn ON parts (mpn);
CREATE INDEX idx_parts_category ON parts (category);
CREATE TABLE movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id INTEGER NOT NULL REFERENCES parts (id) ON DELETE CASCADE,
    delta INTEGER NOT NULL, reason TEXT NOT NULL, ticket_ref TEXT,
    unit_cost_at_time INTEGER, created_at TEXT NOT NULL,
    CONSTRAINT delta_is_nonzero CHECK (delta != 0),
    CONSTRAINT known_reason CHECK (reason IN ('consume', 'correction', 'initial', 'restock')),
    CONSTRAINT cost_not_negative CHECK (unit_cost_at_time IS NULL OR unit_cost_at_time >= 0)
);
CREATE INDEX idx_movements_part ON movements (part_id);
PRAGMA user_version = 1;
"""


@pytest.fixture
def v1_db(tmp_path, monkeypatch):
    """A database exactly as v1 left it, with data in it."""
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    path = tmp_path / "parts.db"
    with connect(path) as conn:
        conn.executescript(_V1_PARTS)
        conn.execute(
            "INSERT INTO parts (mpn, description, category, quantity_on_hand, min_quantity,"
            " unit_cost, created_at, updated_at) VALUES ('IRF7910','MOSFET','mosfet',7,4,187,'t','t')"
        )
        conn.execute(
            "INSERT INTO movements (part_id, delta, reason, ticket_ref, unit_cost_at_time, created_at)"
            " VALUES (1, 10, 'initial', NULL, 187, 't'), (1, -3, 'consume', '26-0001', 187, 't')"
        )
        conn.commit()
    return path


# --- the enum -----------------------------------------------------------------


def test_new_categories_exist() -> None:
    assert to_category("filament") is Category.FILAMENT
    assert to_category("module") is Category.MODULE
    assert Category.FILAMENT.label == "Filament"
    assert Category.MODULE.label == "Module / Board"


def test_schema_version_bumped() -> None:
    assert SCHEMA_VERSION == 2


# --- fresh database ---------------------------------------------------------


def test_fresh_db_accepts_the_new_categories(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    with connect(tmp_path / "parts.db") as conn:
        initialise(conn)
        store = PartStore(conn)

        f = store.create_part(
            description="PETG Black 1 kg", category="filament", quantity_on_hand=2
        )
        m = store.create_part(description="Pico 2 W", category="module")

        assert f.category is Category.FILAMENT
        assert m.category is Category.MODULE
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


# --- the upgrade path -------------------------------------------------------


def test_v1_db_rejects_filament_before_upgrade(v1_db) -> None:
    """Proves the migration is *needed* — the old CHECK really does refuse."""
    with connect(v1_db) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO parts (description, category, created_at, updated_at)"
            " VALUES ('x','filament','t','t')"
        )


def test_upgrade_preserves_parts_and_movements(v1_db) -> None:
    with connect(v1_db) as conn:
        initialise(conn)
        store = PartStore(conn)

        part = store.get_part(1)
        movements = store.list_movements(1)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert part is not None
    assert (part.mpn, part.quantity_on_hand, part.min_quantity, part.unit_cost) == (
        "IRF7910",
        7,
        4,
        187,
    )
    assert [m.delta for m in movements] == [10, -3]
    assert movements[1].ticket_ref == "26-0001"


def test_upgrade_keeps_ids_stable(v1_db) -> None:
    """Movements reference parts by id; a rebuild that renumbered would orphan them."""
    with connect(v1_db) as conn:
        initialise(conn)
        assert conn.execute("SELECT id FROM parts").fetchall() == [(1,)]
        assert conn.execute("SELECT DISTINCT part_id FROM movements").fetchall() == [(1,)]
        # FK still enforced across the rebuild
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO movements (part_id, delta, reason, created_at) VALUES (999, 1, 'restock', 't')"
            )


def test_upgraded_db_accepts_the_new_categories(v1_db) -> None:
    with connect(v1_db) as conn:
        initialise(conn)
        f = PartStore(conn).create_part(description="TPU White", category="filament")

    assert f.category is Category.FILAMENT


def test_upgrade_recreates_indexes(v1_db) -> None:
    with connect(v1_db) as conn:
        initialise(conn)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}

    assert {"idx_parts_mpn", "idx_parts_category", "idx_movements_part"} <= names


def test_upgrade_is_idempotent(v1_db) -> None:
    with connect(v1_db) as conn:
        initialise(conn)
        initialise(conn)
        initialise(conn)

        assert conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM movements").fetchone()[0] == 2
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_upgrade_leaves_no_scratch_table_behind(v1_db) -> None:
    with connect(v1_db) as conn:
        initialise(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    assert not any("new" in t or "old" in t or "tmp" in t for t in tables), tables


def test_upgrade_is_atomic_on_failure(v1_db, monkeypatch) -> None:
    """If the rebuild dies mid-way, the v1 table must still be there and intact."""
    import bench_parts.schema as schema

    original = schema._upgrade_to_v2

    def exploding(conn):
        conn.execute("CREATE TABLE parts_new (id INTEGER PRIMARY KEY)")
        raise sqlite3.OperationalError("simulated failure mid-migration")

    monkeypatch.setattr(schema, "_upgrade_to_v2", exploding)
    with connect(v1_db) as conn, pytest.raises(sqlite3.OperationalError):
        initialise(conn)
    monkeypatch.setattr(schema, "_upgrade_to_v2", original)

    with connect(v1_db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 1
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "parts_new" not in tables
