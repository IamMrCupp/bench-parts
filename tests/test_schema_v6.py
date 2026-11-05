"""Schema v6 — the durable-asset tables, and the v5 → v6 migration.

Unlike v2 (a `parts` rebuild), v6 only *adds* two tables, so there is no data
to copy. The path under test: a v5 database with parts and movements must come
out at v6 with every parts row intact and the new `assets` / `asset_movements`
tables present, their CHECK constraints and FK cascade live.
"""

import sqlite3

import pytest

from bench_parts import schema
from bench_parts.db import connect
from bench_parts.schema import SCHEMA_VERSION, initialise
from bench_parts.store import PartStore


@pytest.fixture
def v5_db(tmp_path, monkeypatch):
    """A database at v5 (parts world, pre-assets) with a part and movements.

    Built from the current parts/movements DDL — v6 doesn't touch those tables,
    so their v5 and v6 shapes are identical; what differs is `user_version` and
    the absence of the asset tables, which is exactly what the migration adds.
    """
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    path = tmp_path / "parts.db"
    with connect(path) as conn:
        conn.execute(schema._CREATE_PARTS)
        conn.execute(schema._CREATE_PARTS_MPN_INDEX)
        conn.execute(schema._CREATE_PARTS_CATEGORY_INDEX)
        conn.execute(schema._CREATE_MOVEMENTS)
        conn.execute(schema._CREATE_MOVEMENTS_PART_INDEX)
        conn.execute("PRAGMA user_version = 5")
        conn.commit()
        PartStore(conn).create_part(
            description="10uF cap", category="capacitor", quantity_on_hand=5, unit_cost=12
        )
    return path


def test_schema_version_at_least_v6() -> None:
    assert SCHEMA_VERSION >= 6


# --- fresh database ---------------------------------------------------------


def test_fresh_db_has_asset_tables_at_current_version(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    with connect(tmp_path / "parts.db") as conn:
        initialise(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"assets", "asset_movements"} <= tables
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_fresh_db_has_asset_indexes(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    with connect(tmp_path / "parts.db") as conn:
        initialise(conn)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {
        "idx_assets_type",
        "idx_assets_assigned_ref",
        "idx_asset_movements_asset",
    } <= names


# --- the upgrade path -------------------------------------------------------


def test_v5_db_has_no_asset_tables_before_upgrade(v5_db) -> None:
    """Proves the migration is *needed*."""
    with connect(v5_db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "assets" not in tables


def test_upgrade_adds_asset_tables_and_preserves_parts(v5_db) -> None:
    with connect(v5_db) as conn:
        initialise(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        part = PartStore(conn).get_part(1)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert {"assets", "asset_movements"} <= tables
    assert part is not None
    assert (part.description, part.quantity_on_hand, part.unit_cost) == ("10uF cap", 5, 12)


def test_upgrade_is_idempotent(v5_db) -> None:
    with connect(v5_db) as conn:
        initialise(conn)
        initialise(conn)
        initialise(conn)
        assert conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_upgrade_leaves_no_foreign_key_violations(v5_db) -> None:
    with connect(v5_db) as conn:
        initialise(conn)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


# --- the constraints the schema carries -------------------------------------


def _fresh(tmp_path):
    with connect(tmp_path / "parts.db") as conn:
        initialise(conn)
    return tmp_path / "parts.db"


def test_asset_type_check_rejects_unknown(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    path = _fresh(tmp_path)
    with connect(path) as conn, pytest.raises(sqlite3.IntegrityError, match="known_asset_type"):
        conn.execute(
            "INSERT INTO assets (name, asset_type, status, created_at, updated_at)"
            " VALUES ('x', 'bogus', 'available', 't', 't')"
        )


def test_asset_status_check_rejects_unknown(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    path = _fresh(tmp_path)
    with connect(path) as conn, pytest.raises(sqlite3.IntegrityError, match="known_asset_status"):
        conn.execute(
            "INSERT INTO assets (name, asset_type, status, created_at, updated_at)"
            " VALUES ('x', 'sdr', 'lost', 't', 't')"
        )


def test_assigned_ref_required_when_assigned(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    path = _fresh(tmp_path)
    with (
        connect(path) as conn,
        pytest.raises(sqlite3.IntegrityError, match="assigned_ref_iff_assigned"),
    ):
        conn.execute(
            "INSERT INTO assets (name, asset_type, status, assigned_ref, created_at, updated_at)"
            " VALUES ('x', 'sdr', 'assigned', NULL, 't', 't')"
        )


def test_assigned_ref_forbidden_when_not_assigned(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    path = _fresh(tmp_path)
    with (
        connect(path) as conn,
        pytest.raises(sqlite3.IntegrityError, match="assigned_ref_iff_assigned"),
    ):
        conn.execute(
            "INSERT INTO assets (name, asset_type, status, assigned_ref, created_at, updated_at)"
            " VALUES ('x', 'sdr', 'available', 'R26-0001', 't', 't')"
        )


def test_asset_action_check_rejects_unknown(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    path = _fresh(tmp_path)
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO assets (name, asset_type, status, created_at, updated_at)"
            " VALUES ('x', 'sdr', 'available', 't', 't')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="known_asset_action"):
            conn.execute(
                "INSERT INTO asset_movements (asset_id, action, created_at)"
                " VALUES (1, 'teleport', 't')"
            )


def test_asset_movements_cascade_on_asset_delete(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    path = _fresh(tmp_path)
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO assets (name, asset_type, status, created_at, updated_at)"
            " VALUES ('x', 'sdr', 'available', 't', 't')"
        )
        conn.execute(
            "INSERT INTO asset_movements (asset_id, action, created_at) VALUES (1, 'acquire', 't')"
        )
        conn.execute("DELETE FROM assets WHERE id = 1")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM asset_movements").fetchone()[0] == 0


def test_asset_movements_fk_enforced(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    path = _fresh(tmp_path)
    with connect(path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO asset_movements (asset_id, action, created_at)"
            " VALUES (999, 'acquire', 't')"
        )


def test_upgrade_is_atomic_on_failure(v5_db, monkeypatch) -> None:
    """If v6 dies mid-way, the DB stays at v5 with no half-built asset tables."""
    original = schema._upgrade_to_v6

    def exploding(conn):
        conn.execute("CREATE TABLE assets (id INTEGER PRIMARY KEY)")
        raise sqlite3.OperationalError("simulated failure mid-migration")

    monkeypatch.setattr(schema, "_upgrade_to_v6", exploding)
    with connect(v5_db) as conn, pytest.raises(sqlite3.OperationalError):
        initialise(conn)
    monkeypatch.setattr(schema, "_upgrade_to_v6", original)

    with connect(v5_db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "assets" not in tables
