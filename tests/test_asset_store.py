"""The asset store — lifecycle, the ledger-is-truth invariant, and the guards.

Mirrors the discipline `tests/test_store.py` pins for parts: no status changes
without a ledger row, the cache is rebuildable from the log, and every illegal
transition fails with a readable message rather than a silent wrong state.
"""

import pytest

from bench_parts.asset_store import AssetStore
from bench_parts.vocabulary import AssetAction, AssetStatus


def _make(store: AssetStore, **kw):
    kw.setdefault("name", "HackRF One #1")
    kw.setdefault("asset_type", "sdr")
    return store.create_asset(**kw)


# --- creation ----------------------------------------------------------------


def test_create_starts_available_with_no_assignment(asset_store) -> None:
    a = _make(asset_store, serial="RS-1234", location="SDR drawer")
    assert a.status is AssetStatus.AVAILABLE
    assert a.assigned_ref is None
    assert a.serial == "RS-1234"


def test_create_writes_an_acquire_ledger_row(asset_store) -> None:
    """Exactly like parts' `initial`: no asset exists without a ledger row."""
    a = _make(asset_store)
    ledger = asset_store.list_asset_movements(a.id)
    assert [m.action for m in ledger] == [AssetAction.ACQUIRE]


def test_create_requires_a_name(asset_store) -> None:
    with pytest.raises(ValueError, match="name is required"):
        asset_store.create_asset(name="  ", asset_type="sdr")


def test_create_rejects_unknown_type(asset_store) -> None:
    with pytest.raises(ValueError, match="asset type"):
        asset_store.create_asset(name="x", asset_type="radio")


def test_blank_optional_fields_normalise_to_none(asset_store) -> None:
    a = _make(asset_store, serial="  ", notes="")
    assert a.serial is None
    assert a.notes is None


# --- the happy lifecycle -----------------------------------------------------


def test_assign_then_return_round_trip(asset_store) -> None:
    a = _make(asset_store)
    a = asset_store.assign(a.id, "R26-0004", note="RF work")
    assert a.status is AssetStatus.ASSIGNED
    assert a.assigned_ref == "R26-0004"

    a = asset_store.return_asset(a.id)
    assert a.status is AssetStatus.AVAILABLE
    assert a.assigned_ref is None

    actions = [m.action for m in asset_store.list_asset_movements(a.id)]
    assert actions == [AssetAction.ACQUIRE, AssetAction.ASSIGN, AssetAction.RETURN]
    assign_row = asset_store.list_asset_movements(a.id)[1]
    assert assign_row.engagement_ref == "R26-0004"
    assert assign_row.note == "RF work"


def test_repair_round_trip(asset_store) -> None:
    a = _make(asset_store)
    a = asset_store.send_for_repair(a.id, note="flaky usb")
    assert a.status is AssetStatus.OUT_FOR_REPAIR
    a = asset_store.return_from_repair(a.id)
    assert a.status is AssetStatus.AVAILABLE


def test_repair_out_allowed_from_assigned_and_clears_ref(asset_store) -> None:
    a = _make(asset_store)
    a = asset_store.assign(a.id, "R26-0004")
    a = asset_store.send_for_repair(a.id, note="died mid-engagement")
    assert a.status is AssetStatus.OUT_FOR_REPAIR
    assert a.assigned_ref is None


def test_retire_and_unretire(asset_store) -> None:
    a = _make(asset_store)
    a = asset_store.retire_asset(a.id)
    assert a.status is AssetStatus.RETIRED
    a = asset_store.unretire_asset(a.id)
    assert a.status is AssetStatus.AVAILABLE


def test_retire_clears_assignment(asset_store) -> None:
    a = _make(asset_store)
    a = asset_store.assign(a.id, "R26-0004")
    a = asset_store.retire_asset(a.id)
    assert a.status is AssetStatus.RETIRED
    assert a.assigned_ref is None


# --- the guards --------------------------------------------------------------


def test_assign_requires_a_ref(asset_store) -> None:
    a = _make(asset_store)
    with pytest.raises(ValueError, match="engagement ref is required"):
        asset_store.assign(a.id, "   ")


def test_cannot_assign_an_already_assigned_asset(asset_store) -> None:
    a = _make(asset_store)
    asset_store.assign(a.id, "R26-0004")
    with pytest.raises(ValueError, match="assigned to R26-0004"):
        asset_store.assign(a.id, "R26-0009")


def test_cannot_assign_a_retired_asset(asset_store) -> None:
    a = _make(asset_store)
    asset_store.retire_asset(a.id)
    with pytest.raises(ValueError, match="retired"):
        asset_store.assign(a.id, "R26-0004")


def test_cannot_return_an_unassigned_asset(asset_store) -> None:
    a = _make(asset_store)
    with pytest.raises(ValueError, match="not assigned"):
        asset_store.return_asset(a.id)


def test_cannot_repair_in_an_asset_not_out_for_repair(asset_store) -> None:
    a = _make(asset_store)
    with pytest.raises(ValueError, match="not out for repair"):
        asset_store.return_from_repair(a.id)


def test_cannot_send_retired_for_repair(asset_store) -> None:
    a = _make(asset_store)
    asset_store.retire_asset(a.id)
    with pytest.raises(ValueError, match="only an available or assigned"):
        asset_store.send_for_repair(a.id)


def test_retire_is_idempotent(asset_store) -> None:
    a = _make(asset_store)
    asset_store.retire_asset(a.id)
    before = len(asset_store.list_asset_movements(a.id))
    again = asset_store.retire_asset(a.id)
    assert again.status is AssetStatus.RETIRED
    assert len(asset_store.list_asset_movements(a.id)) == before  # no second row


def test_unretire_on_a_live_asset_is_a_noop(asset_store) -> None:
    a = _make(asset_store)
    before = len(asset_store.list_asset_movements(a.id))
    asset_store.unretire_asset(a.id)
    assert len(asset_store.list_asset_movements(a.id)) == before


def test_operations_on_missing_asset_raise_lookup(asset_store) -> None:
    with pytest.raises(LookupError):
        asset_store.assign(999, "R26-0004")


# --- listing & filters -------------------------------------------------------


def test_list_filters_by_type(asset_store) -> None:
    _make(asset_store, name="HackRF", asset_type="sdr")
    _make(asset_store, name="J-Link", asset_type="debugger")
    sdrs = asset_store.list_assets(asset_type="sdr")
    assert [a.name for a in sdrs] == ["HackRF"]


def test_list_filters_by_assigned_ref_case_insensitively(asset_store) -> None:
    a = _make(asset_store, name="HackRF")
    b = _make(asset_store, name="J-Link", asset_type="debugger")
    asset_store.assign(a.id, "R26-0004")
    asset_store.assign(b.id, "R26-0009")
    on_4 = asset_store.list_assets(assigned_ref="r26-0004")
    assert [x.name for x in on_4] == ["HackRF"]


def test_list_excludes_retired_by_default(asset_store) -> None:
    _make(asset_store, name="HackRF")
    b = _make(asset_store, name="Old RTL", asset_type="sdr")
    asset_store.retire_asset(b.id)
    names = {x.name for x in asset_store.list_assets()}
    assert names == {"HackRF"}
    with_retired = {x.name for x in asset_store.list_assets(include_retired=True)}
    assert with_retired == {"HackRF", "Old RTL"}


def test_list_filters_by_status(asset_store) -> None:
    a = _make(asset_store, name="HackRF")
    _make(asset_store, name="RTL", asset_type="sdr")
    asset_store.assign(a.id, "R26-0004")
    assigned = asset_store.list_assets(status="assigned")
    assert [x.name for x in assigned] == ["HackRF"]


def test_list_rejects_unknown_type(asset_store) -> None:
    with pytest.raises(ValueError):
        asset_store.list_assets(asset_type="radio")


def test_engagement_refs_are_distinct_and_sorted(asset_store) -> None:
    a = _make(asset_store, name="HackRF")
    b = _make(asset_store, name="J-Link", asset_type="debugger")
    c = _make(asset_store, name="RTL", asset_type="sdr")
    asset_store.assign(a.id, "R26-0009")
    asset_store.assign(b.id, "R26-0004")
    asset_store.assign(c.id, "R26-0004")
    assert asset_store.list_engagement_refs() == ["R26-0004", "R26-0009"]


def test_recent_movements_newest_first_with_asset(asset_store) -> None:
    a = _make(asset_store, name="HackRF")
    asset_store.assign(a.id, "R26-0004")
    recent = asset_store.list_recent_asset_movements(limit=5)
    assert recent[0].movement.action is AssetAction.ASSIGN
    assert recent[0].asset.name == "HackRF"


def test_recent_movements_filter_by_engagement(asset_store) -> None:
    a = _make(asset_store, name="HackRF")
    b = _make(asset_store, name="J-Link", asset_type="debugger")
    asset_store.assign(a.id, "R26-0004")
    asset_store.assign(b.id, "R26-0009")
    rows = asset_store.list_recent_asset_movements(engagement_ref="R26-0004")
    assert {r.asset.name for r in rows} == {"HackRF"}


# --- update ------------------------------------------------------------------


def test_update_changes_only_passed_fields(asset_store) -> None:
    a = _make(asset_store, name="HackRF", location="drawer A", notes="keep")
    asset_store.update_asset(a.id, location="drawer B")
    updated = asset_store.get_asset(a.id)
    assert updated.location == "drawer B"
    assert updated.notes == "keep"
    assert updated.name == "HackRF"


def test_update_can_clear_a_field_with_none(asset_store) -> None:
    a = _make(asset_store, notes="scratch")
    asset_store.update_asset(a.id, notes=None)
    assert asset_store.get_asset(a.id).notes is None


def test_update_has_no_status_parameter(asset_store) -> None:
    """Status moves only through the lifecycle methods — update can't smuggle it."""
    import inspect

    params = inspect.signature(asset_store.update_asset).parameters
    assert "status" not in params
    assert "assigned_ref" not in params


def test_update_requires_a_non_blank_name(asset_store) -> None:
    a = _make(asset_store)
    with pytest.raises(ValueError, match="name is required"):
        asset_store.update_asset(a.id, name="  ")


# --- delete ------------------------------------------------------------------


def test_delete_a_never_assigned_asset(asset_store) -> None:
    a = _make(asset_store)
    asset_store.delete_asset(a.id)
    assert asset_store.get_asset(a.id) is None


def test_delete_refused_once_assigned(asset_store) -> None:
    a = _make(asset_store)
    asset_store.assign(a.id, "R26-0004")
    asset_store.return_asset(a.id)
    with pytest.raises(ValueError, match="engagement history"):
        asset_store.delete_asset(a.id)
    assert asset_store.get_asset(a.id) is not None  # still there


# --- rebuild (ledger is the source of truth) ---------------------------------


def test_rebuild_is_a_noop_when_cache_matches_ledger(asset_store) -> None:
    a = _make(asset_store)
    asset_store.assign(a.id, "R26-0004")
    assert asset_store.rebuild_statuses() == {}


def test_rebuild_repairs_a_drifted_cache(asset_store) -> None:
    a = _make(asset_store)
    asset_store.assign(a.id, "R26-0004")
    # Corrupt the cache behind the store's back, as a stray script might.
    asset_store._conn.execute(
        "UPDATE assets SET status = 'available', assigned_ref = NULL WHERE id = ?", (a.id,)
    )
    asset_store._conn.commit()

    changed = asset_store.rebuild_statuses()
    assert changed == {a.id: ("available", "assigned")}
    fixed = asset_store.get_asset(a.id)
    assert fixed.status is AssetStatus.ASSIGNED
    assert fixed.assigned_ref == "R26-0004"


def test_rebuild_matches_the_latest_ledger_row(asset_store) -> None:
    """Across a full lifecycle, the rebuilt status is the last transition's."""
    a = _make(asset_store)
    asset_store.assign(a.id, "R26-0004")
    asset_store.return_asset(a.id)
    asset_store.send_for_repair(a.id)
    asset_store._conn.execute("UPDATE assets SET status = 'assigned', assigned_ref = 'X'")
    asset_store._conn.commit()
    asset_store.rebuild_statuses()
    assert asset_store.get_asset(a.id).status is AssetStatus.OUT_FOR_REPAIR
