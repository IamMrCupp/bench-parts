"""Seed the asset registry with the bench's real durable test gear.

The source of truth is ``bench-inventory`` (§1 Test & measurement) plus a shelf
walk — the same approach the parts catalogue was seeded with. Two differences
from the parts seed worth stating out loud:

1. **Only what's genuinely owned.** The bench-recon asset *types* (SDRs, JTAG/SWD
   debuggers, Flipper Zero, Proxmark, CAN/OBD-II interfaces, chip clips…) are the
   kit the bench is *becoming* — most of that gear isn't acquired yet. This seed
   does **not** invent it. The two owned items that map to a dedicated type are
   the DSLogic Plus (logic analyzer) and the XGecu T48 (flash programmer); the
   rest of the owned, assignable test instruments go in as ``other`` until the
   type list grows to fit them. Add the recon gear here as it's bought.

2. **Locations are the walk list.** bench-inventory records *what* the bench owns,
   not which drawer each unit sits in, so every seeded asset starts with no
   location — the asset equivalent of the parts seed's "0 pending a shelf-walk".
   Fill them in from the asset's page as you walk the bench.

Idempotent on ``name``: running it again adds only assets that aren't already
there, so it's safe to re-run after the walk or after adding a line here.

Run against the configured database::

    BENCH_PARTS_DB_PATH=/path/to/parts.db uv run python -m bench_parts.seed_assets
"""

from __future__ import annotations

import os
from pathlib import Path

from bench_parts.asset_store import AssetStore
from bench_parts.db import connect
from bench_parts.schema import initialise

#: The real durable test gear, from bench-inventory §1 (status ✅ owned) plus the
#: two dedicated-type items. ``serial`` is left unset — bench-inventory doesn't
#: record unit serials; add them from each asset's page. ``location`` is left
#: unset deliberately (see the module docstring — it's the walk list).
SEED_ASSETS: list[dict[str, object]] = [
    # --- maps to a dedicated asset type -------------------------------------
    {
        "name": "DSLogic Plus",
        "asset_type": "logic_analyzer",
        "manufacturer": "DreamSourceLab",
        "model": "DSLogic Plus",
        "notes": "16 ch, 400 MHz, 16 Gbit depth. DSView on Mac/Linux.",
    },
    {
        "name": "XGecu T48",
        "asset_type": "flash_programmer",
        "manufacturer": "XGecu",
        "model": "T48 (TL866-3G)",
        "notes": "EEPROM/ROM programmer + 12-adapter bundle. minipro on Mac/Linux.",
    },
    # --- owned, assignable instruments; 'other' until the type list grows ----
    {
        "name": "YEAPOOK ADS1014D",
        "asset_type": "other",
        "manufacturer": "YEAPOOK",
        "model": "ADS1014D",
        "notes": "Oscilloscope + DDS sig-gen, 100 MHz, 1 GSa/s, 2 ch. USB mass-storage only.",
    },
    {
        "name": "OWON SPM8104",
        "asset_type": "other",
        "manufacturer": "OWON",
        "model": "SPM8104",
        "notes": "Bench PSU + DMM, 0-80 V / 0-10 A / 400 W, CC/OVP/OCP, SCPI over USB.",
    },
    {
        "name": "HR1520 injection box",
        "asset_type": "other",
        "model": "HR1520",
        "notes": "0-20 V / 0-20 A short-killer / current injection.",
    },
    {
        "name": "FNIRSI LCR-P1",
        "asset_type": "other",
        "manufacturer": "FNIRSI",
        "model": "LCR-P1",
        "notes": "Transistor / MOSFET / cap / diode / ESR / LCR tester.",
    },
    {
        "name": "AstroAI DMM (10000-count)",
        "asset_type": "other",
        "manufacturer": "AstroAI",
        "notes": "10000-count TRMS multimeter, colour LCD.",
    },
    {
        "name": "AstroAI DMM (6000-count)",
        "asset_type": "other",
        "manufacturer": "AstroAI",
        "notes": "6000-count TRMS multimeter.",
    },
    {
        "name": "ENGINDOT bench PSU",
        "asset_type": "other",
        "manufacturer": "ENGINDOT",
        "notes": "30 V / 5 A adjustable, CV/CC, 4-digit. Secondary supply behind the SPM8104.",
    },
    {
        "name": "Sipeed T256s thermal camera",
        "asset_type": "other",
        "manufacturer": "Sipeed",
        "model": "T256s",
        "notes": "256x192 LWIR (AI to 640x480), <50 mK, 5 cm macro, UVC over USB-C.",
    },
]


def seed(store: AssetStore) -> dict[str, list[str]]:
    """Create any seed asset not already present, matched by name.

    Idempotent: an asset whose ``name`` already exists is left untouched (its
    edits, assignment and history are the live record and must not be clobbered).
    Returns ``{"added": [...], "skipped": [...]}`` of names, so a caller — or a
    test — can see exactly what it did.
    """
    existing = {a.name for a in store.list_assets(include_retired=True)}
    added: list[str] = []
    skipped: list[str] = []
    for spec in SEED_ASSETS:
        name = str(spec["name"])
        if name in existing:
            skipped.append(name)
            continue
        store.create_asset(**spec)  # type: ignore[arg-type]
        added.append(name)
    return {"added": added, "skipped": skipped}


def _db_path() -> Path:
    """Where the database lives — the same env var the app reads, resolved here
    so this script doesn't import ``bench_parts.app`` (which needs the auth env
    just to import)."""
    return Path(os.environ.get("BENCH_PARTS_DB_PATH", "parts.db"))


def main() -> None:  # pragma: no cover - thin CLI wrapper over seed()
    path = _db_path()
    with connect(path) as conn:
        initialise(conn)
        result = seed(AssetStore(conn))
    print(f"seeded {path}: added {len(result['added'])}, skipped {len(result['skipped'])}")
    for name in result["added"]:
        print(f"  + {name}")
    for name in result["skipped"]:
        print(f"  = {name} (already present)")


if __name__ == "__main__":  # pragma: no cover
    main()
