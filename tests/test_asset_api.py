"""The asset JSON API — what benchhud's interface monitor codes against.

Exercises the same two things the parts API tests do: the auth (bearer token or
browser session, both must work; everything else 401) and the JSON shape and
filters, plus the assign/return write path and its guards.
"""

import pytest
from fastapi.testclient import TestClient

from bench_parts.app import app
from bench_parts.asset_api import ASSET_KEYS, ASSET_MOVEMENT_KEYS, to_api_asset
from bench_parts.auth import API_TOKEN_ENV_VAR

TOKEN = "test-api-token-not-a-real-one"


@pytest.fixture(autouse=True)
def _configure_token(monkeypatch):
    monkeypatch.setenv(API_TOKEN_ENV_VAR, TOKEN)


@pytest.fixture
def api(db_path):
    """A bare client — no session. The API is reached with a bearer token."""
    return TestClient(app)


@pytest.fixture
def seeded(asset_store):
    hackrf = asset_store.create_asset(
        name="HackRF One #1",
        asset_type="sdr",
        manufacturer="Great Scott Gadgets",
        serial="RS-1234",
        location="SDR drawer",
    )
    jlink = asset_store.create_asset(name="J-Link EDU", asset_type="debugger", serial="JL-77")
    rtl = asset_store.create_asset(name="RTL-SDR v3", asset_type="sdr")
    asset_store.assign(hackrf.id, "R26-0004", note="RF recon")
    asset_store.assign(jlink.id, "R26-0004")
    return hackrf, jlink, rtl


def _auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


# --- auth -------------------------------------------------------------------


def test_bearer_token_grants_access(api, seeded) -> None:
    assert api.get("/api/assets", headers=_auth()).status_code == 200


def test_absent_auth_is_401_json_not_a_redirect(api, seeded) -> None:
    response = api.get("/api/assets", follow_redirects=False)
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


@pytest.mark.parametrize("header", ["Bearer wrong", "Basic dXNlcjpwYXNz", TOKEN, "Bearer"])
def test_wrong_or_malformed_auth_is_401(api, seeded, header) -> None:
    assert api.get("/api/assets", headers={"Authorization": header}).status_code == 401


def test_a_session_also_grants_access(client, seeded) -> None:
    assert client.get("/api/assets").status_code == 200


def test_no_configured_token_fails_closed(api, seeded, monkeypatch) -> None:
    monkeypatch.delenv(API_TOKEN_ENV_VAR)
    assert api.get("/api/assets", headers=_auth("anything")).status_code == 401


# --- the projection ---------------------------------------------------------


def test_asset_json_has_exactly_the_documented_keys(api, seeded) -> None:
    body = api.get("/api/assets", headers=_auth()).json()
    assert set(body[0]) == set(ASSET_KEYS)


def test_asset_json_values(api, seeded) -> None:
    hackrf, _, _ = seeded
    body = api.get(f"/api/assets/{hackrf.id}", headers=_auth()).json()
    assert body["id"] == hackrf.id
    assert body["name"] == "HackRF One #1"
    assert body["asset_type"] == "sdr"
    assert body["asset_type_label"] == "SDR"
    assert body["status"] == "assigned"
    assert body["status_label"] == "Assigned"
    assert body["assigned_ref"] == "R26-0004"
    assert body["serial"] == "RS-1234"
    assert body["url"].endswith(f"/assets/{hackrf.id}")


def test_nulls_are_json_null(api, seeded) -> None:
    _, _, rtl = seeded
    body = api.get(f"/api/assets/{rtl.id}", headers=_auth()).json()
    assert body["manufacturer"] is None
    assert body["serial"] is None
    assert body["assigned_ref"] is None


def test_projection_is_pure(seeded) -> None:
    hackrf, _, _ = seeded
    projected = to_api_asset(hackrf, "http://bench.local/")
    assert projected["url"] == f"http://bench.local/assets/{hackrf.id}"
    assert set(projected) == set(ASSET_KEYS)


# --- listing and filtering --------------------------------------------------


def test_list_returns_every_live_asset(api, seeded) -> None:
    assert len(api.get("/api/assets", headers=_auth()).json()) == 3


def test_filter_by_assigned_ref(api, seeded) -> None:
    """The interface-monitor query: gear tied to an engagement."""
    body = api.get("/api/assets", params={"assigned_ref": "R26-0004"}, headers=_auth()).json()
    assert {a["name"] for a in body} == {"HackRF One #1", "J-Link EDU"}


def test_filter_by_type(api, seeded) -> None:
    body = api.get("/api/assets", params={"asset_type": "sdr"}, headers=_auth()).json()
    assert {a["name"] for a in body} == {"HackRF One #1", "RTL-SDR v3"}


def test_filter_by_status(api, seeded) -> None:
    body = api.get("/api/assets", params={"status": "available"}, headers=_auth()).json()
    assert {a["name"] for a in body} == {"RTL-SDR v3"}


def test_unknown_type_is_400_json(api, seeded) -> None:
    response = api.get("/api/assets", params={"asset_type": "radio"}, headers=_auth())
    assert response.status_code == 400
    assert "radio" in response.json()["detail"]


def test_unknown_asset_is_404_json(api, seeded) -> None:
    response = api.get("/api/assets/9999", headers=_auth())
    assert response.status_code == 404


def test_retired_hidden_unless_requested(api, seeded, asset_store) -> None:
    _, _, rtl = seeded
    asset_store.retire_asset(rtl.id)
    assert len(api.get("/api/assets", headers=_auth()).json()) == 2
    assert len(api.get("/api/assets", params={"include_retired": "1"}, headers=_auth()).json()) == 3


# --- movements --------------------------------------------------------------


def test_asset_movements_oldest_first(api, seeded) -> None:
    hackrf, _, _ = seeded
    body = api.get(f"/api/assets/{hackrf.id}/movements", headers=_auth()).json()
    assert [m["action"] for m in body] == ["acquire", "assign"]
    assert body[1]["engagement_ref"] == "R26-0004"
    assert set(body[0]) == set(ASSET_MOVEMENT_KEYS)


# --- assign / return (the write path) ---------------------------------------


def test_assign_moves_an_available_asset(api, seeded) -> None:
    _, _, rtl = seeded
    response = api.post(
        f"/api/assets/{rtl.id}/assign",
        json={"engagement_ref": "R26-0009", "note": "wardrive"},
        headers=_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "assigned"
    assert body["assigned_ref"] == "R26-0009"


def test_assign_then_return_round_trip(api, seeded) -> None:
    _, _, rtl = seeded
    api.post(f"/api/assets/{rtl.id}/assign", json={"engagement_ref": "R26-0009"}, headers=_auth())
    response = api.post(f"/api/assets/{rtl.id}/return", json={}, headers=_auth())
    assert response.status_code == 200
    assert response.json()["status"] == "available"
    assert response.json()["assigned_ref"] is None


def test_assign_an_already_assigned_asset_is_400(api, seeded) -> None:
    hackrf, _, _ = seeded
    response = api.post(
        f"/api/assets/{hackrf.id}/assign", json={"engagement_ref": "R26-0009"}, headers=_auth()
    )
    assert response.status_code == 400
    assert "assigned" in response.json()["detail"]


def test_return_an_unassigned_asset_is_400(api, seeded) -> None:
    _, _, rtl = seeded
    response = api.post(f"/api/assets/{rtl.id}/return", json={}, headers=_auth())
    assert response.status_code == 400


def test_assign_missing_asset_is_404(api, seeded) -> None:
    response = api.post(
        "/api/assets/9999/assign", json={"engagement_ref": "R26-0009"}, headers=_auth()
    )
    assert response.status_code == 404


def test_assign_requires_json_body_not_a_form(api, seeded) -> None:
    """The CSRF story: a cross-site form can't send application/json, so a
    form-encoded body is 422'd before the endpoint runs."""
    _, _, rtl = seeded
    response = api.post(
        f"/api/assets/{rtl.id}/assign", data={"engagement_ref": "R26-0009"}, headers=_auth()
    )
    assert response.status_code == 422


def test_assign_rejects_unknown_fields(api, seeded) -> None:
    _, _, rtl = seeded
    response = api.post(
        f"/api/assets/{rtl.id}/assign",
        json={"engagement_ref": "R26-0009", "bogus": 1},
        headers=_auth(),
    )
    assert response.status_code == 422


def test_write_requires_auth(api, seeded) -> None:
    _, _, rtl = seeded
    response = api.post(
        f"/api/assets/{rtl.id}/assign", json={"engagement_ref": "R26-0009"}, follow_redirects=False
    )
    assert response.status_code == 401
