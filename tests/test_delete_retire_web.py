"""Delete and retire from the UI and the API."""

import pytest
from fastapi.testclient import TestClient

from bench_parts.api import PART_KEYS
from bench_parts.app import app
from bench_parts.auth import API_TOKEN_ENV_VAR

TOKEN = "test-api-token-not-a-real-one"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv(API_TOKEN_ENV_VAR, TOKEN)


@pytest.fixture
def api(db_path):
    return TestClient(app)


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def fresh(store):
    return store.create_part(
        description="Mis-add", category="other", mpn="OOPS", quantity_on_hand=2
    )


@pytest.fixture
def used(store):
    p = store.create_part(
        description="Used part", category="mosfet", mpn="IRF7910", quantity_on_hand=5
    )
    store.consume(p.id, 1, ticket_ref="26-0001")
    return p


# --- UI: buttons ------------------------------------------------------------


def test_detail_page_offers_retire_and_delete(client, fresh) -> None:
    body = client.get(f"/parts/{fresh.id}").text

    assert f'action="/parts/{fresh.id}/retire"' in body
    assert f'action="/parts/{fresh.id}/delete"' in body


def test_delete_button_is_hidden_once_used(client, used) -> None:
    """Don't offer a button that will only ever say no."""
    body = client.get(f"/parts/{used.id}").text

    assert f'action="/parts/{used.id}/retire"' in body
    assert f'action="/parts/{used.id}/delete"' not in body


def test_retired_part_page_shows_a_banner_and_unretire(client, store, used) -> None:
    store.retire_part(used.id)

    body = client.get(f"/parts/{used.id}").text

    assert "retired" in body.lower()
    assert f'action="/parts/{used.id}/unretire"' in body
    assert f'action="/parts/{used.id}/retire"' not in body


def test_retired_part_page_hides_stock_controls(client, store, used) -> None:
    store.retire_part(used.id)

    body = client.get(f"/parts/{used.id}").text

    assert 'value="consume"' not in body


# --- UI: actions ------------------------------------------------------------


def test_delete_from_ui_redirects_to_catalogue(client, store, fresh) -> None:
    response = client.post(f"/parts/{fresh.id}/delete", data={}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert store.get_part(fresh.id) is None


def test_delete_from_ui_refuses_a_used_part(client, store, used) -> None:
    response = client.post(f"/parts/{used.id}/delete", data={}, follow_redirects=False)

    assert response.status_code == 400
    assert "retire" in response.text.lower()
    assert store.get_part(used.id) is not None


def test_retire_from_ui(client, store, used) -> None:
    response = client.post(f"/parts/{used.id}/retire", data={}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/parts/{used.id}"
    assert store.get_part(used.id).retired


def test_unretire_from_ui(client, store, used) -> None:
    store.retire_part(used.id)

    client.post(f"/parts/{used.id}/unretire", data={}, follow_redirects=False)

    assert not store.get_part(used.id).retired


def test_retired_part_leaves_the_catalogue_and_order_page(client, store) -> None:
    p = store.create_part(
        description="Old", category="other", mpn="OLD", quantity_on_hand=0, min_quantity=3
    )
    assert "OLD" in client.get("/").text
    assert "OLD" in client.get("/order").text

    client.post(f"/parts/{p.id}/retire", data={})

    assert "OLD" not in client.get("/").text
    assert "OLD" not in client.get("/order").text


def test_catalogue_can_show_retired(client, store) -> None:
    p = store.create_part(description="Old", category="other", mpn="OLD")
    store.retire_part(p.id)

    body = client.get("/", params={"show_retired": "1"}).text

    assert "OLD" in body
    assert "retired" in body.lower()


def test_catalogue_links_to_show_retired_only_when_there_are_some(client, store) -> None:
    assert "show_retired" not in client.get("/").text
    p = store.create_part(description="Old", category="other", mpn="OLD")
    store.retire_part(p.id)

    assert "show_retired=1" in client.get("/").text


def test_ui_actions_need_a_session_and_a_token(anonymous, store, fresh, db_path) -> None:
    from conftest import sign_in

    assert (
        anonymous.post(f"/parts/{fresh.id}/delete", data={}, follow_redirects=False).status_code
        == 303
    )
    raw = sign_in(TestClient(app))
    assert raw.post(f"/parts/{fresh.id}/delete", data={}).status_code == 403
    assert store.get_part(fresh.id) is not None


# --- API --------------------------------------------------------------------


def test_part_json_carries_retired_at(api, fresh) -> None:
    body = api.get(f"/api/parts/{fresh.id}", headers=_auth()).json()

    assert "retired_at" in PART_KEYS
    assert body["retired_at"] is None


def test_api_delete(api, store, fresh) -> None:
    response = api.delete(f"/api/parts/{fresh.id}", headers=_auth())

    assert response.status_code == 204
    assert store.get_part(fresh.id) is None


def test_api_delete_refuses_a_used_part(api, store, used) -> None:
    response = api.delete(f"/api/parts/{used.id}", headers=_auth())

    assert response.status_code == 409
    assert "retire" in response.json()["detail"]
    assert store.get_part(used.id) is not None


def test_api_delete_unknown_is_404(api) -> None:
    assert api.delete("/api/parts/9999", headers=_auth()).status_code == 404


def test_api_delete_needs_auth(api, store, fresh) -> None:
    assert api.delete(f"/api/parts/{fresh.id}").status_code == 401
    assert store.get_part(fresh.id) is not None


def test_api_retire_and_unretire(api, store, used) -> None:
    r = api.post(f"/api/parts/{used.id}/retire", headers=_auth())
    assert r.status_code == 200
    assert r.json()["retired_at"] is not None
    assert store.get_part(used.id).retired

    r = api.post(f"/api/parts/{used.id}/unretire", headers=_auth())
    assert r.status_code == 200
    assert r.json()["retired_at"] is None


def test_api_list_hides_retired_unless_asked(api, store, used, fresh) -> None:
    store.retire_part(used.id)

    default = api.get("/api/parts", headers=_auth()).json()
    everything = api.get("/api/parts", params={"include_retired": "1"}, headers=_auth()).json()

    assert [p["id"] for p in default] == [fresh.id]
    assert {p["id"] for p in everything} == {fresh.id, used.id}


def test_api_below_min_excludes_retired(api, store) -> None:
    p = store.create_part(description="Old", category="other", quantity_on_hand=0, min_quantity=3)
    store.retire_part(p.id)

    assert api.get("/api/parts", params={"below_min": "1"}, headers=_auth()).json() == []


def test_api_consume_on_retired_is_400(api, store, used) -> None:
    store.retire_part(used.id)

    r = api.post(f"/api/parts/{used.id}/consume", json={"quantity": 1}, headers=_auth())

    assert r.status_code == 400
    assert "retired" in r.json()["detail"]


def test_api_ticket_history_survives_retire(api, store, used) -> None:
    """The whole point of retire-not-delete."""
    store.retire_part(used.id)

    body = api.get("/api/movements", params={"ticket_ref": "26-0001"}, headers=_auth()).json()

    assert len(body) == 1
    assert body[0]["part_id"] == used.id
