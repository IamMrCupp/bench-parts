"""The asset web UI — catalogue, add/edit, and the lifecycle controls.

Driven through the signed-in `client` fixture, the way a tablet at the bench
would. The store itself is tested in `test_asset_store.py`; these tests are
about the routes, the rendered pages, and that each control writes through the
store.
"""

from bench_parts.asset_store import AssetStore
from bench_parts.db import connect


def _store(db_path) -> AssetStore:
    return AssetStore(connect(db_path))


# --- catalogue ---------------------------------------------------------------


def test_nav_has_assets_tab(client) -> None:
    assert 'href="/assets"' in client.get("/").text


def test_empty_catalogue_invites_adding(client) -> None:
    body = client.get("/assets").text
    assert "No assets yet" in body


def test_catalogue_lists_assets(client, asset_store) -> None:
    asset_store.create_asset(name="HackRF One #1", asset_type="sdr", location="SDR drawer")
    body = client.get("/assets").text
    assert "HackRF One #1" in body
    assert "SDR" in body
    assert "SDR drawer" in body


def test_catalogue_filters_by_type(client, asset_store) -> None:
    asset_store.create_asset(name="HackRF", asset_type="sdr")
    asset_store.create_asset(name="J-Link", asset_type="debugger")
    body = client.get("/assets", params={"asset_type": "sdr"}).text
    assert "HackRF" in body
    assert "J-Link" not in body


def test_catalogue_filters_by_assigned_ref(client, asset_store) -> None:
    a = asset_store.create_asset(name="HackRF", asset_type="sdr")
    asset_store.create_asset(name="RTL", asset_type="sdr")
    asset_store.assign(a.id, "R26-0004")
    body = client.get("/assets", params={"assigned_ref": "R26-0004"}).text
    assert "HackRF" in body
    assert "RTL" not in body


def test_catalogue_unknown_type_is_400(client) -> None:
    assert client.get("/assets", params={"asset_type": "radio"}).status_code == 400


def test_retired_hidden_until_toggled(client, asset_store) -> None:
    a = asset_store.create_asset(name="Old RTL", asset_type="sdr")
    asset_store.retire_asset(a.id)
    assert "Old RTL" not in client.get("/assets").text
    assert "Old RTL" in client.get("/assets", params={"show_retired": "1"}).text


# --- add ---------------------------------------------------------------------


def test_add_form_renders(client) -> None:
    body = client.get("/assets/new").text
    assert "Add an asset" in body
    assert 'name="asset_type"' in body


def test_create_asset_redirects_to_detail(client, db_path) -> None:
    response = client.post(
        "/assets",
        data={"name": "HackRF One #1", "asset_type": "sdr", "serial": "RS-1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/assets/")
    assets = _store(db_path).list_assets()
    assert [a.name for a in assets] == ["HackRF One #1"]
    assert assets[0].status.value == "available"


def test_create_rejection_keeps_input(client) -> None:
    response = client.post("/assets", data={"name": "", "asset_type": "sdr"})
    assert response.status_code == 400
    assert "name is required" in response.text


# --- detail & lifecycle ------------------------------------------------------


def test_detail_shows_lifecycle_controls_for_available(client, asset_store) -> None:
    a = asset_store.create_asset(name="HackRF", asset_type="sdr")
    body = client.get(f"/assets/{a.id}").text
    assert f'action="/assets/{a.id}/assign"' in body
    assert "Send for repair" in body


def test_assign_from_ui(client, asset_store, db_path) -> None:
    a = asset_store.create_asset(name="HackRF", asset_type="sdr")
    response = client.post(
        f"/assets/{a.id}/assign",
        data={"engagement_ref": "R26-0004", "note": "RF work"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    refreshed = _store(db_path).get_asset(a.id)
    assert refreshed.status.value == "assigned"
    assert refreshed.assigned_ref == "R26-0004"


def test_return_from_ui(client, asset_store, db_path) -> None:
    a = asset_store.create_asset(name="HackRF", asset_type="sdr")
    asset_store.assign(a.id, "R26-0004")
    client.post(f"/assets/{a.id}/return", data={}, follow_redirects=False)
    assert _store(db_path).get_asset(a.id).status.value == "available"


def test_assign_an_assigned_asset_shows_error(client, asset_store) -> None:
    a = asset_store.create_asset(name="HackRF", asset_type="sdr")
    asset_store.assign(a.id, "R26-0004")
    response = client.post(f"/assets/{a.id}/assign", data={"engagement_ref": "R26-0009"})
    assert response.status_code == 400
    assert "assigned" in response.text


def test_repair_round_trip_from_ui(client, asset_store, db_path) -> None:
    a = asset_store.create_asset(name="HackRF", asset_type="sdr")
    client.post(f"/assets/{a.id}/repair-out", data={"note": "flaky"}, follow_redirects=False)
    assert _store(db_path).get_asset(a.id).status.value == "out_for_repair"
    client.post(f"/assets/{a.id}/repair-in", data={}, follow_redirects=False)
    assert _store(db_path).get_asset(a.id).status.value == "available"


def test_edit_asset_fields(client, asset_store, db_path) -> None:
    a = asset_store.create_asset(name="HackRF", asset_type="sdr", location="drawer A")
    client.post(
        f"/assets/{a.id}",
        data={"name": "HackRF One", "asset_type": "sdr", "location": "drawer B"},
        follow_redirects=False,
    )
    refreshed = _store(db_path).get_asset(a.id)
    assert refreshed.name == "HackRF One"
    assert refreshed.location == "drawer B"


def test_retire_and_unretire_from_ui(client, asset_store, db_path) -> None:
    a = asset_store.create_asset(name="HackRF", asset_type="sdr")
    client.post(f"/assets/{a.id}/retire", data={}, follow_redirects=False)
    assert _store(db_path).get_asset(a.id).status.value == "retired"
    client.post(f"/assets/{a.id}/unretire", data={}, follow_redirects=False)
    assert _store(db_path).get_asset(a.id).status.value == "available"


def test_delete_a_never_assigned_asset(client, asset_store, db_path) -> None:
    a = asset_store.create_asset(name="scratch", asset_type="other")
    response = client.post(f"/assets/{a.id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert _store(db_path).get_asset(a.id) is None


def test_delete_refused_after_assignment(client, asset_store, db_path) -> None:
    a = asset_store.create_asset(name="HackRF", asset_type="sdr")
    asset_store.assign(a.id, "R26-0004")
    asset_store.return_asset(a.id)
    response = client.post(f"/assets/{a.id}/delete")
    assert response.status_code == 400
    assert "engagement history" in response.text
    assert _store(db_path).get_asset(a.id) is not None


def test_delete_button_absent_once_assigned(client, asset_store) -> None:
    a = asset_store.create_asset(name="HackRF", asset_type="sdr")
    asset_store.assign(a.id, "R26-0004")
    asset_store.return_asset(a.id)
    body = client.get(f"/assets/{a.id}").text
    assert f'action="/assets/{a.id}/delete"' not in body


def test_missing_asset_is_404(client) -> None:
    assert client.get("/assets/9999").status_code == 404
