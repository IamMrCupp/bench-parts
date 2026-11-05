"""The asset seed — real bench gear, loaded idempotently.

The seed itself is data; these tests pin the two properties that matter: it
creates valid, available assets, and it's safe to re-run (idempotent on name).
"""

from bench_parts.asset_store import AssetStore
from bench_parts.seed_assets import SEED_ASSETS, seed
from bench_parts.vocabulary import AssetStatus, AssetType, to_asset_type


def test_every_seed_entry_names_a_known_type() -> None:
    """A typo'd asset_type would fail at create time against the DB CHECK;
    catch it here instead, before anyone runs the loader against prod."""
    for spec in SEED_ASSETS:
        assert to_asset_type(spec["asset_type"]) in AssetType


def test_every_seed_entry_has_a_name() -> None:
    names = [spec["name"] for spec in SEED_ASSETS]
    assert all(names)
    assert len(names) == len(set(names)), "seed names must be unique — they're the idempotency key"


def test_seed_creates_all_entries_available(asset_store: AssetStore) -> None:
    result = seed(asset_store)
    assert len(result["added"]) == len(SEED_ASSETS)
    assert result["skipped"] == []
    assets = asset_store.list_assets()
    assert len(assets) == len(SEED_ASSETS)
    assert all(a.status is AssetStatus.AVAILABLE for a in assets)


def test_seed_is_idempotent(asset_store: AssetStore) -> None:
    seed(asset_store)
    second = seed(asset_store)
    assert second["added"] == []
    assert len(second["skipped"]) == len(SEED_ASSETS)
    # No duplicates created on the second run.
    assert len(asset_store.list_assets()) == len(SEED_ASSETS)


def test_seed_does_not_clobber_an_edited_asset(asset_store: AssetStore) -> None:
    """Re-running after a unit has been assigned must not reset it."""
    seed(asset_store)
    dslogic = next(a for a in asset_store.list_assets() if a.name == "DSLogic Plus")
    asset_store.assign(dslogic.id, "R26-0004")

    seed(asset_store)  # re-run
    refreshed = asset_store.get_asset(dslogic.id)
    assert refreshed.status is AssetStatus.ASSIGNED
    assert refreshed.assigned_ref == "R26-0004"


def test_the_two_dedicated_type_items_are_present(asset_store: AssetStore) -> None:
    seed(asset_store)
    by_name = {a.name: a for a in asset_store.list_assets()}
    assert by_name["DSLogic Plus"].asset_type is AssetType.LOGIC_ANALYZER
    assert by_name["XGecu T48"].asset_type is AssetType.FLASH_PROGRAMMER
