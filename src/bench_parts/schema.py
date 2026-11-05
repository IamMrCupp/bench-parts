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

from bench_parts.vocabulary import (
    AssetAction,
    AssetStatus,
    AssetType,
    Category,
    MovementReason,
    sql_in_list,
)

SCHEMA_VERSION = 6


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

    -- v3: soft-delete. NULL = live. A retired part is hidden from the catalogue
    -- and the "to order" list but keeps every movement, so job costing stays
    -- answerable. Hard delete is only allowed with no consumption history.
    retired_at       TEXT,

    -- v4: which project / area a part belongs to ("Audiophore", "bench",
    -- "3D printing"). Free text, matched case-insensitively; NULL = unsorted.
    project          TEXT,

    -- v5: ordering. supplier_pn is the supplier's own part number (exact for
    -- their bulk-add tools, where an MPN is ambiguous). reorder_qty is what you
    -- actually buy — the pack, or "back to 2x min" — rather than the bare
    -- shortfall. Both optional.
    supplier_pn      TEXT,
    reorder_qty      INTEGER,

    CONSTRAINT known_category CHECK (category IN ({sql_in_list(Category)})),

    -- The backstop behind the store's "can't consume more than you have" guard.
    -- If this ever fires, the cache and the log have diverged.
    CONSTRAINT quantity_not_negative CHECK (quantity_on_hand >= 0),
    CONSTRAINT min_quantity_not_negative CHECK (min_quantity >= 0),
    CONSTRAINT unit_cost_not_negative CHECK (unit_cost IS NULL OR unit_cost >= 0),
    CONSTRAINT reorder_qty_positive CHECK (reorder_qty IS NULL OR reorder_qty > 0)
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


# --- assets (v6) -------------------------------------------------------------
#
# Durable, serialized test gear — assigned to an engagement and returned, never
# consumed. A separate model from `parts` on purpose: the fields differ (a
# serial and a single unit, not a count) and the ledger records lifecycle
# transitions, not signed stock deltas. Consumables stay in `parts`, untouched.
#
# `status` and `assigned_ref` are a CACHE; `asset_movements` is the source of
# truth, exactly as `movements` is for `parts.quantity_on_hand`. The store
# updates both in one transaction and can rebuild the cache from the log.
_CREATE_ASSETS = f"""
CREATE TABLE IF NOT EXISTS assets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,

    -- A human name for the unit: "HackRF One #2", "J-Link EDU". Required —
    -- it's what you read off the shelf and off the HUD.
    name          TEXT    NOT NULL,
    asset_type    TEXT    NOT NULL,

    manufacturer  TEXT,
    model         TEXT,
    -- Serial / unit identifier. The best discriminator between two otherwise
    -- identical probes — see the interface-monitor port-binding note. Nullable
    -- and NOT unique: salvaged or unmarked gear has none.
    serial        TEXT,
    location      TEXT,
    notes         TEXT,

    -- Cache of the latest lifecycle state, derived from asset_movements and
    -- updated in the same transaction as the ledger insert.
    status        TEXT    NOT NULL DEFAULT '{AssetStatus.AVAILABLE.value}',

    -- The engagement this asset is out on, e.g. "R26-0004". Stored verbatim and
    -- NEVER validated against bench-recon — loose coupling across services, the
    -- same discipline as movements.ticket_ref.
    assigned_ref  TEXT,

    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,

    CONSTRAINT known_asset_type CHECK (asset_type IN ({sql_in_list(AssetType)})),
    CONSTRAINT known_asset_status CHECK (status IN ({sql_in_list(AssetStatus)})),

    -- An engagement ref is set exactly when assigned. The backstop behind the
    -- store's in-transaction cache update: if this ever fires, the cache and
    -- the ledger have diverged.
    CONSTRAINT assigned_ref_iff_assigned CHECK (
        (status = '{AssetStatus.ASSIGNED.value}' AND assigned_ref IS NOT NULL)
        OR (status != '{AssetStatus.ASSIGNED.value}' AND assigned_ref IS NULL)
    )
);
"""

_CREATE_ASSETS_TYPE_INDEX = "CREATE INDEX IF NOT EXISTS idx_assets_type ON assets (asset_type);"
# The filter benchhud's interface monitor hits: gear tied to an engagement.
_CREATE_ASSETS_ASSIGNED_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_assets_assigned_ref ON assets (assigned_ref);"
)

# The append-only asset ledger. Every lifecycle transition lands here; no UPDATE
# or DELETE path should ever touch it. That's what makes the status cache
# rebuildable and an asset's custody reconstructable after the fact.
_CREATE_ASSET_MOVEMENTS = f"""
CREATE TABLE IF NOT EXISTS asset_movements (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id       INTEGER NOT NULL REFERENCES assets (id) ON DELETE CASCADE,

    action         TEXT    NOT NULL,

    -- The engagement ref on an `assign` row; NULL for every other action.
    engagement_ref TEXT,

    -- Optional free-text context ("left with client", "flaky USB port").
    note           TEXT,

    created_at     TEXT    NOT NULL,

    CONSTRAINT known_asset_action CHECK (action IN ({sql_in_list(AssetAction)}))
);
"""

_CREATE_ASSET_MOVEMENTS_ASSET_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_asset_movements_asset ON asset_movements (asset_id);"
)


def _create_assets(conn: sqlite3.Connection) -> None:
    """Create the asset tables and their indexes. Idempotent (``IF NOT EXISTS``)."""
    conn.execute(_CREATE_ASSETS)
    conn.execute(_CREATE_ASSETS_TYPE_INDEX)
    conn.execute(_CREATE_ASSETS_ASSIGNED_INDEX)
    conn.execute(_CREATE_ASSET_MOVEMENTS)
    conn.execute(_CREATE_ASSET_MOVEMENTS_ASSET_INDEX)


def _upgrade_to_v2(conn: sqlite3.Connection) -> None:
    """The v1 → v2 table rebuild — extend the category CHECK (Filament, Module).

    Called inside an explicit transaction with foreign keys OFF (see
    :func:`_migrate`); this function only does the DDL/DML. Rows are copied
    **preserving ids** — movements reference parts by id, and renumbering would
    orphan the ledger. ``_CREATE_PARTS`` is reused with the table name swapped,
    so the rebuilt table is always exactly the current schema: no second copy of
    the DDL to drift.
    """
    conn.execute(
        _CREATE_PARTS.replace("CREATE TABLE IF NOT EXISTS parts", "CREATE TABLE parts_new")
    )
    columns = (
        "id, mpn, manufacturer, description, category, quantity_on_hand, min_quantity,"
        " unit_cost, supplier, supplier_url, location, notes, created_at, updated_at"
    )
    conn.execute(f"INSERT INTO parts_new ({columns}) SELECT {columns} FROM parts")
    conn.execute("DROP TABLE parts")
    conn.execute("ALTER TABLE parts_new RENAME TO parts")
    conn.execute(_CREATE_PARTS_MPN_INDEX)
    conn.execute(_CREATE_PARTS_CATEGORY_INDEX)


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """``ALTER TABLE ADD COLUMN``, but a no-op when the column already exists.

    A database that arrives here via the v2 *rebuild* already has the column
    (``_CREATE_PARTS`` is the current schema); one that was genuinely at v2
    doesn't. Checking ``table_info`` rather than catching the duplicate-column
    error keeps the migration honest either way.
    """
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _upgrade_to_v3(conn: sqlite3.Connection) -> None:
    """v2 → v3: add ``retired_at``. A plain add-column — no rebuild needed."""
    _add_column_if_missing(conn, "parts", "retired_at", "TEXT")


def _upgrade_to_v4(conn: sqlite3.Connection) -> None:
    """v3 → v4: add ``project``. Add-column, idempotent."""
    _add_column_if_missing(conn, "parts", "project", "TEXT")


def _upgrade_to_v5(conn: sqlite3.Connection) -> None:
    """v4 → v5: ``supplier_pn`` and ``reorder_qty``. Add-columns, idempotent.
    (The CHECK on reorder_qty only applies to fresh tables — ADD COLUMN can't
    carry a table CHECK; the store validates it, which is the path that matters.)"""
    _add_column_if_missing(conn, "parts", "supplier_pn", "TEXT")
    _add_column_if_missing(conn, "parts", "reorder_qty", "INTEGER")


def _upgrade_to_v6(conn: sqlite3.Connection) -> None:
    """v5 → v6: the durable-asset model — two brand-new tables.

    Nothing existing is touched: this is a pure additive ``CREATE``, not a
    rebuild, so there's no data to copy and no ids to preserve. It still runs
    through :func:`_migrate` for the atomic ``BEGIN IMMEDIATE`` / ``user_version``
    bump and the post-migration foreign-key check — the proven path, even where
    the FK-off-around-a-rebuild half of it is a no-op for a plain create.
    """
    _create_assets(conn)


def _migrate(conn: sqlite3.Connection, step, version: int) -> None:
    """Run one table-rebuild migration step atomically and FK-safely.

    Two SQLite facts shape this, both learned by a failing test:

    1. **``DROP TABLE`` cascades.** It's an implicit ``DELETE``, so with foreign
       keys on it would wipe every movement — and ``defer_foreign_keys`` does
       *not* help. The documented procedure is ``PRAGMA foreign_keys = OFF``
       around the rebuild, then ``PRAGMA foreign_key_check`` afterwards. That
       pragma is a no-op inside a transaction, so it has to be set first.
    2. **Python's ``sqlite3`` does not open a transaction for DDL** (legacy
       isolation), so ``with conn:`` would leave a half-built ``parts_new``
       behind on failure. An explicit ``BEGIN IMMEDIATE`` / ``COMMIT`` /
       ``ROLLBACK`` makes the rebuild genuinely atomic.
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            step(conn)
            conn.execute(f"PRAGMA user_version = {version}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")

    problems = conn.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise RuntimeError(
            f"schema migration to v{version} left foreign-key violations: {problems}"
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

    if current == 0:
        # Fresh database: the CREATEs carry the current schema, including the
        # v2 category list and the v6 asset tables — nothing to migrate.
        with conn:
            conn.execute(_CREATE_PARTS)
            conn.execute(_CREATE_PARTS_MPN_INDEX)
            conn.execute(_CREATE_PARTS_CATEGORY_INDEX)
            conn.execute(_CREATE_MOVEMENTS)
            conn.execute(_CREATE_MOVEMENTS_PART_INDEX)
            _create_assets(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return

    # Existing database: apply each step it's missing, in order, each one
    # atomic on its own. A failure part-way leaves it at the last good version.
    if current < 2:
        _migrate(conn, _upgrade_to_v2, 2)
    if current < 3:
        _migrate(conn, _upgrade_to_v3, 3)
    if current < 4:
        _migrate(conn, _upgrade_to_v4, 4)
    if current < 5:
        _migrate(conn, _upgrade_to_v5, 5)
    if current < 6:
        _migrate(conn, _upgrade_to_v6, 6)
