"""The read side of the parts API.

Two things get exercised hard: the auth (a machine client uses a bearer token,
not the browser session — and both must work) and the shape of the JSON, which
is what benchhud's `InventorySource` will code against.
"""

import pytest
from fastapi.testclient import TestClient

from bench_parts.api import MOVEMENT_KEYS, PART_KEYS, to_api_part
from bench_parts.app import app
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
def seeded(store):
    a = store.create_part(
        description="Dual N-channel MOSFET",
        category="mosfet",
        mpn="IRF7910",
        manufacturer="Infineon",
        quantity_on_hand=12,
        min_quantity=4,
        unit_cost=187,
        supplier="Digi-Key",
        supplier_url="https://example.invalid/irf7910",
        location="A3",
        notes="watch the tab",
    )
    b = store.create_part(
        description="Buck controller",
        category="ic",
        mpn="TPS51980",
        quantity_on_hand=2,
        min_quantity=5,
    )
    c = store.create_part(
        description="Salvaged cap", category="capacitor", quantity_on_hand=0, min_quantity=10
    )
    store.consume(a.id, 3, ticket_ref="26-0001")
    store.consume(b.id, 1, ticket_ref="26-0001")
    store.consume(a.id, 1, ticket_ref="26-0002")
    return a, b, c


def _auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


# --- auth -------------------------------------------------------------------


def test_bearer_token_grants_access(api, seeded) -> None:
    assert api.get("/api/parts", headers=_auth()).status_code == 200


def test_absent_auth_is_401_json_not_a_redirect(api, seeded) -> None:
    response = api.get("/api/parts", follow_redirects=False)

    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


@pytest.mark.parametrize("header", ["Bearer wrong", "Basic dXNlcjpwYXNz", TOKEN, "Bearer"])
def test_wrong_or_malformed_auth_is_401(api, seeded, header) -> None:
    assert api.get("/api/parts", headers={"Authorization": header}).status_code == 401


def test_a_session_also_grants_access(client, seeded) -> None:
    """A signed-in human can curl it from the browser's session."""
    assert client.get("/api/parts").status_code == 200


def test_no_configured_token_fails_closed(api, seeded, monkeypatch) -> None:
    """Unset token: bearer always rejects — never 'any token works'."""
    monkeypatch.delenv(API_TOKEN_ENV_VAR)

    assert api.get("/api/parts", headers=_auth("anything")).status_code == 401
    assert api.get("/api/parts", headers=_auth(TOKEN)).status_code == 401


def test_no_configured_token_still_allows_a_session(client, seeded, monkeypatch) -> None:
    monkeypatch.delenv(API_TOKEN_ENV_VAR)

    assert client.get("/api/parts").status_code == 200


# --- the projection ---------------------------------------------------------


def test_part_json_has_exactly_the_documented_keys(api, seeded) -> None:
    body = api.get("/api/parts", headers=_auth()).json()

    assert set(body[0]) == set(PART_KEYS)


def test_part_json_values(api, seeded) -> None:
    a, _, _ = seeded
    body = api.get(f"/api/parts/{a.id}", headers=_auth()).json()

    assert body["id"] == a.id
    assert body["mpn"] == "IRF7910"
    assert body["manufacturer"] == "Infineon"
    assert body["category"] == "mosfet"
    assert body["category_label"] == "MOSFET"
    assert body["quantity_on_hand"] == 8  # 12 - 3 - 1
    assert body["min_quantity"] == 4
    assert body["status"] == "ok"
    assert body["unit_cost_cents"] == 187
    assert body["supplier_url"] == "https://example.invalid/irf7910"
    assert body["location"] == "A3"
    assert body["url"].endswith(f"/parts/{a.id}")


def test_status_is_derived_the_same_way_as_the_ui(api, seeded) -> None:
    _, b, c = seeded
    parts = {p["id"]: p for p in api.get("/api/parts", headers=_auth()).json()}

    assert parts[b.id]["status"] == "low"
    assert parts[c.id]["status"] == "out"


def test_cost_is_integer_cents_never_a_float(api, seeded) -> None:
    body = api.get("/api/parts", headers=_auth()).json()

    for part in body:
        assert part["unit_cost_cents"] is None or isinstance(part["unit_cost_cents"], int)


def test_nulls_are_json_null_not_empty_strings(api, seeded) -> None:
    _, _, c = seeded
    body = api.get(f"/api/parts/{c.id}", headers=_auth()).json()

    assert body["mpn"] is None
    assert body["supplier"] is None
    assert body["unit_cost_cents"] is None


def test_projection_is_pure(seeded) -> None:
    """Callable without a request — the single place shape is defined."""
    a, _, _ = seeded
    projected = to_api_part(a, "http://bench.local/")

    assert projected["url"] == f"http://bench.local/parts/{a.id}"
    assert set(projected) == set(PART_KEYS)


# --- listing and filtering --------------------------------------------------


def test_list_returns_every_part(api, seeded) -> None:
    assert len(api.get("/api/parts", headers=_auth()).json()) == 3


def test_below_min_returns_the_to_order_set_most_urgent_first(api, seeded) -> None:
    _, b, c = seeded
    body = api.get("/api/parts", params={"below_min": "1"}, headers=_auth()).json()

    assert [p["id"] for p in body] == [c.id, b.id]  # out before low


def test_below_min_agrees_with_the_order_page(api, client, seeded) -> None:
    api_ids = {
        p["mpn"] for p in api.get("/api/parts", params={"below_min": "1"}, headers=_auth()).json()
    }
    page = client.get("/order").text

    for mpn in api_ids - {None}:
        assert mpn in page
    assert "IRF7910" not in page  # ok, so on neither


def test_search_and_category_filter(api, seeded) -> None:
    assert [
        p["mpn"] for p in api.get("/api/parts", params={"q": "irf"}, headers=_auth()).json()
    ] == ["IRF7910"]
    assert [
        p["mpn"] for p in api.get("/api/parts", params={"category": "ic"}, headers=_auth()).json()
    ] == ["TPS51980"]


def test_unknown_category_is_400_json(api, seeded) -> None:
    response = api.get("/api/parts", params={"category": "mosfets"}, headers=_auth())

    assert response.status_code == 400
    assert "mosfets" in response.json()["detail"]


def test_unknown_part_is_404_json(api, seeded) -> None:
    response = api.get("/api/parts/9999", headers=_auth())

    assert response.status_code == 404
    assert "detail" in response.json()


# --- movements --------------------------------------------------------------


def test_part_movements_oldest_first(api, seeded) -> None:
    a, _, _ = seeded
    body = api.get(f"/api/parts/{a.id}/movements", headers=_auth()).json()

    assert [m["reason"] for m in body] == ["initial", "consume", "consume"]
    assert [m["delta"] for m in body] == [12, -3, -1]
    assert set(body[0]) == set(MOVEMENT_KEYS)


def test_movements_by_ticket_ref(api, seeded) -> None:
    """The costing query: everything used on one job."""
    body = api.get("/api/movements", params={"ticket_ref": "26-0001"}, headers=_auth()).json()

    assert len(body) == 2
    assert all(m["ticket_ref"] == "26-0001" for m in body)
    spend = sum(-m["delta"] * (m["unit_cost_cents_at_time"] or 0) for m in body)
    assert spend == 3 * 187  # TPS had no cost


def test_movements_by_unknown_ticket_is_empty_not_404(api, seeded) -> None:
    """A ref bench-parts has never seen isn't an error — it's a job that used no parts."""
    response = api.get("/api/movements", params={"ticket_ref": "99-9999"}, headers=_auth())

    assert response.status_code == 200
    assert response.json() == []


def test_movements_requires_a_ticket_ref(api, seeded) -> None:
    """Dumping the whole ledger isn't a use case; a missing filter is a 400."""
    assert api.get("/api/movements", headers=_auth()).status_code == 400
