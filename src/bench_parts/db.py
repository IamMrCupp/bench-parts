"""SQLite connection setup.

The production database lives on an **NFS-backed PVC** on the rpi-k8s cluster.
That single fact drives everything here:

- **WAL does not work on NFS.** It's SQLite's usual default and the usual advice,
  and on a network filesystem it fails by corrupting rather than erroring. Under
  ``BENCH_PARTS_SQLITE_NFS=1`` this module uses ``journal_mode=TRUNCATE`` and
  ``synchronous=FULL`` instead.
- **Exactly one writer.** The Deployment stays at ``replicas: 1`` with
  ``strategy: Recreate``. Nothing here can enforce that, but it's the assumption
  the pragma choice is built on.

Local development keeps WAL, which is faster and perfectly safe on a real disk.
The env var name mirrors benchhud-intake's ``INTAKE_SQLITE_NFS`` and recipe-box's
``RCM_SQLITE_NFS`` so all three apps on the cluster read the same way.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

NFS_ENV_VAR = "BENCH_PARTS_SQLITE_NFS"


def _on_nfs() -> bool:
    return os.environ.get(NFS_ENV_VAR, "") not in ("", "0")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a configured connection to the parts database.

    Callers get rows accessible by column name and foreign keys enforced, which
    SQLite otherwise leaves off.
    """
    # check_same_thread=False because FastAPI runs a sync dependency and the
    # sync endpoint it feeds on *different* threadpool threads. Without this,
    # concurrent requests fail with "SQLite objects created in a thread can only
    # be used in that same thread".
    #
    # Safe here because a connection is created per request and never shared
    # between them: the threads touch it in sequence, never concurrently. It
    # would NOT be safe with a single module-level connection.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    if _on_nfs():
        # TRUNCATE keeps the rollback journal as a plain file that's truncated
        # on commit — no shared-memory index, which is the part WAL needs and
        # NFS can't provide reliably.
        conn.execute("PRAGMA journal_mode = TRUNCATE")
        conn.execute("PRAGMA synchronous = FULL")
    else:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")

    # Movements reference parts by foreign key, and SQLite ignores foreign keys
    # unless asked — an orphaned movement would silently corrupt the stock
    # history this whole design exists to keep trustworthy.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
