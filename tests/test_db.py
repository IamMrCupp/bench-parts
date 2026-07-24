"""Connection pragmas are what they claim to be.

These assertions read the pragmas back rather than trusting that setting them
worked. `journal_mode` in particular silently refuses some transitions and
returns whatever mode it actually landed in — a test that only executes the
PRAGMA statement would happily pass against a database in the wrong mode.

Getting this wrong isn't a crash, it's corruption on NFS. Hence the paranoia,
and hence `test_nfs_mode_writes_no_wal_sidecars`, which checks the filesystem
rather than asking SQLite to grade its own homework.
"""

import sqlite3

import pytest

from bench_parts.db import NFS_ENV_VAR, connect


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "parts.db"


def _pragma(conn: sqlite3.Connection, name: str):
    return conn.execute(f"PRAGMA {name}").fetchone()[0]


def _set_mode(monkeypatch, nfs: str | None) -> None:
    if nfs is None:
        monkeypatch.delenv(NFS_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(NFS_ENV_VAR, nfs)


def test_nfs_mode_uses_truncate_journal(db_path, monkeypatch) -> None:
    _set_mode(monkeypatch, "1")

    with connect(db_path) as conn:
        assert _pragma(conn, "journal_mode") == "truncate"


def test_nfs_mode_uses_full_synchronous(db_path, monkeypatch) -> None:
    """FULL is 2. Anything less risks a torn write surviving a power cut."""
    _set_mode(monkeypatch, "1")

    with connect(db_path) as conn:
        assert _pragma(conn, "synchronous") == 2


def test_local_mode_uses_wal(db_path, monkeypatch) -> None:
    """Without the flag this is a dev box, where WAL is faster and safe."""
    _set_mode(monkeypatch, None)

    with connect(db_path) as conn:
        assert _pragma(conn, "journal_mode") == "wal"


def test_never_wal_in_nfs_mode(db_path, monkeypatch) -> None:
    """The specific failure this whole module exists to prevent."""
    _set_mode(monkeypatch, "1")

    with connect(db_path) as conn:
        assert _pragma(conn, "journal_mode") != "wal"


def test_nfs_mode_writes_no_wal_sidecars(db_path, monkeypatch) -> None:
    """Filesystem evidence, independent of what SQLite reports about itself.

    A real write has to happen first — the journal mode only manifests on disk
    once there's a transaction to journal.
    """
    _set_mode(monkeypatch, "1")

    with connect(db_path) as conn:
        conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO probe (id) VALUES (1)")
        conn.commit()

        sidecars = sorted(p.name for p in db_path.parent.iterdir() if p.name != db_path.name)

        assert not any(name.endswith(("-wal", "-shm")) for name in sidecars), sidecars


def test_local_mode_writes_a_wal_sidecar(db_path, monkeypatch) -> None:
    """The mirror image, so the NFS assertion above can't pass vacuously."""
    _set_mode(monkeypatch, None)

    with connect(db_path) as conn:
        conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO probe (id) VALUES (1)")
        conn.commit()

        sidecars = sorted(p.name for p in db_path.parent.iterdir() if p.name != db_path.name)

        assert any(name.endswith("-wal") for name in sidecars), sidecars


@pytest.mark.parametrize("nfs", ["1", None])
def test_foreign_keys_enforced_in_both_modes(db_path, monkeypatch, nfs) -> None:
    """SQLite ships with foreign keys OFF, which surprises everyone once.

    Movements reference parts, so this one is load-bearing rather than tidy.
    """
    _set_mode(monkeypatch, nfs)

    with connect(db_path) as conn:
        assert _pragma(conn, "foreign_keys") == 1


def test_rows_are_mappings(db_path, monkeypatch) -> None:
    """Column access by name, so callers don't index into tuples positionally."""
    _set_mode(monkeypatch, None)

    with connect(db_path) as conn:
        row = conn.execute("SELECT 42 AS answer").fetchone()

        assert row["answer"] == 42


@pytest.mark.parametrize("value", ["", "0"])
def test_falsey_flag_values_mean_local(db_path, monkeypatch, value) -> None:
    """Empty and "0" are the two ways a deployment says "not NFS"."""
    _set_mode(monkeypatch, value)

    with connect(db_path) as conn:
        assert _pragma(conn, "journal_mode") == "wal"
