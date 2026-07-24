"""Database schema and migration.

**Migrations use ``PRAGMA user_version`` and idempotent DDL, not Alembic.** Two
tables, a single writer, one deployment — Alembic would add a dependency and a
migrations tree for a problem this app doesn't have. ``user_version`` is a
SQLite built-in that costs nothing and is easy to replace later if the schema
gets complicated.

**The CHECK constraints are enforced here as well as in Python.** The same rules
live in ``bench_parts.vocabulary`` and the store, but the database is the layer
that survives a future script, a manual ``sqlite3`` session, or a bug in the
Phase 4 API. Their value lists are *generated* from the enums, so the two can't
drift.

**The movements log is the source of truth; ``parts.quantity_on_hand`` is a
cache.** The store updates both in one transaction, and can rebuild the cache
from the log. Nothing in the schema can enforce that pairing — it's the store's
job — but the constraints here make a drifted cache impossible to hide behind a
negative number.
"""

from __future__ import annotations

import sqlite3

from bench_parts.vocabulary import Category, MovementReason, sql_in_list

SCHEMA_VERSION = 1


_CREATE_PARTS = f"""
CREATE TABLE IF NOT EXISTS parts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Manufacturer part number: the search key when you're buying stock.
    --
    -- Deliberately NOT UNIQUE, and nullable. Salvaged and generic parts have no
    -- clean MPN, and the same MPN legitimately spans manufacturers. Adding a
    -- UNIQUE constraint here would reject real shop inventory — tests/test_schema.py
    -- pins this behaviour so a well-meaning "fix" fails loudly.
    mpn              TEXT,
    manufacturer     TEXT,

    description      TEXT    NOT NULL,
    category         TEXT    NOT NULL,

    -- Cache of SUM(delta) over this part's movements, updated in the same
    -- transaction as the movement insert. Rebuildable — see store.rebuild_quantities.
    quantity_on_hand INTEGER NOT NULL DEFAULT 0,

    -- At or below this, the part shows up in the "to order" view. 0 means
    -- "never flag as low" — only an actual zero count is interesting.
    min_quantity     INTEGER NOT NULL DEFAULT 0,

    -- Integer CENTS, never a float. Current/last price paid; consumption
    -- snapshots it onto the movement so job costing survives price changes.
    unit_cost        INTEGER,

    supplier         TEXT,
    supplier_url     TEXT,
    location         TEXT,
    notes            TEXT,

    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,

    CONSTRAINT known_category CHECK (category IN ({sql_in_list(Category)})),

    -- The backstop behind the store's "can't consume more than you have" guard.
    -- If this ever fires, the cache and the log have diverged.
    CONSTRAINT quantity_not_negative CHECK (quantity_on_hand >= 0),
    CONSTRAINT min_quantity_not_negative CHECK (min_quantity >= 0),
    CONSTRAINT unit_cost_not_negative CHECK (unit_cost IS NULL OR unit_cost >= 0)
);
"""

# The search key, and the filter the catalogue groups by.
_CREATE_PARTS_MPN_INDEX = "CREATE INDEX IF NOT EXISTS idx_parts_mpn ON parts (mpn);"
_CREATE_PARTS_CATEGORY_INDEX = "CREATE INDEX IF NOT EXISTS idx_parts_category ON parts (category);"

# The append-only stock ledger. Every quantity change lands here.
#
# No UPDATE or DELETE path should ever touch this table: a correction is a new
# row with an opposing delta, not an edit of history. That's what makes the
# cache rebuildable and a job's parts cost reconstructable after the fact.
_CREATE_MOVEMENTS = f"""
CREATE TABLE IF NOT EXISTS movements (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id           INTEGER NOT NULL REFERENCES parts (id) ON DELETE CASCADE,

    -- Signed: positive restocks, negative consumes, either for a correction.
    delta             INTEGER NOT NULL,
    reason            TEXT    NOT NULL,

    -- The job this was used on, e.g. "26-0001". Stored verbatim and NEVER
    -- validated against benchhud-intake — loose coupling across services is the
    -- point, exactly as benchhud stores external_ref.
    ticket_ref        TEXT,

    -- Snapshot of the part's unit_cost (CENTS) at the moment of the movement,
    -- so a later price change can't rewrite what a past job cost.
    unit_cost_at_time INTEGER,

    created_at        TEXT    NOT NULL,

    -- A movement that moves nothing is a bug, not a record.
    CONSTRAINT delta_is_nonzero CHECK (delta != 0),
    CONSTRAINT known_reason CHECK (reason IN ({sql_in_list(MovementReason)})),
    CONSTRAINT cost_not_negative CHECK (unit_cost_at_time IS NULL OR unit_cost_at_time >= 0)
);
"""

# Every rebuild and every part-history read groups by part_id.
_CREATE_MOVEMENTS_PART_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_movements_part ON movements (part_id);"
)


def initialise(conn: sqlite3.Connection) -> None:
    """Create or upgrade the schema. Safe to call on every startup.

    Base tables use ``IF NOT EXISTS`` (idempotent create); future changes to an
    *existing* table should be version-gated ``ALTER``s that no-op when already
    applied. A database at the current version short-circuits. No downgrade path
    — rolling a schema back is a restore-from-backup problem, not a migration
    one.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return

    with conn:
        conn.execute(_CREATE_PARTS)
        conn.execute(_CREATE_PARTS_MPN_INDEX)
        conn.execute(_CREATE_PARTS_CATEGORY_INDEX)
        conn.execute(_CREATE_MOVEMENTS)
        conn.execute(_CREATE_MOVEMENTS_PART_INDEX)

        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
