"""Recording consumption over the API — `POST /api/parts/{id}/consume`.

The claim under test: a movement recorded from the HUD is indistinguishable
from one recorded by hand on the tablet. Every assertion about stock reads the
ledger through the store, and one test checks the UI's own history page shows
the API's row.
"""

import pytest
from fastapi.testclient import TestClient

from bench_parts.app import app
from bench_parts.auth import API_TOKEN_ENV_VAR

TOKEN = "test-api-token-not-a-real-one"


@pytest.fixture(autouse=True)
def _configure_token(monkeypatch):
    monkeypatch.setenv(API_TOKEN_ENV_VAR, TOKEN)


@pytest.fixture
def api(db_path):
    return TestClient(app)


@pytest.fixture
def part(store):
    return store.create_part(
        description="Dual N-channel MOSFET",
        category="mosfet",
        mpn="IRF7910",
        quantity_on_hand=10,
        min_quantity=4,
        unit_cost=187,
    )


def _auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def _consume(api, part, quantity, ticket_ref=None, **kw):
    body = {"quantity": quantity}
    if ticket_ref is not None:
        body["ticket_ref"] = ticket_ref
    return api.post(f"/api/parts/{part.id}/consume", json=body, headers=_auth(), **kw)


# --- the happy path ---------------------------------------------------------


def test_consume_moves_the_count_and_returns_part_and_movement(api, part, store) -> None:
    response = _consume(api, part, 3, ticket_ref="26-0001")

    assert response.status_code == 200
    body = response.json()
    assert body["part"]["quantity_on_hand"] == 7
    assert body["part"]["status"] == "ok"
    assert body["movement"]["delta"] == -3
    assert body["movement"]["reason"] == "consume"
    assert body["movement"]["ticket_ref"] == "26-0001"
    assert body["movement"]["unit_cost_cents_at_time"] == 187
    assert store.get_part(part.id).quantity_on_hand == 7


def test_consume_writes_exactly_one_ledger_row(api, part, store) -> None:
    _consume(api, part, 3, ticket_ref="26-0001")

    movements = store.list_movements(part.id)

    assert len(movements) == 2  # initial + this
    assert movements[-1].delta == -3
    assert movements[-1].ticket_ref == "26-0001"


def test_ticket_ref_is_optional_and_stored_verbatim(api, part, store) -> None:
    _consume(api, part, 1)
    _consume(api, part, 1, ticket_ref="not-a-real-ref")

    refs = [m.ticket_ref for m in store.list_movements(part.id)]
    assert refs[-2:] == [None, "not-a-real-ref"]


def test_blank_ticket_ref_is_null(api, part, store) -> None:
    _consume(api, part, 1, ticket_ref="   ")

    assert store.list_movements(part.id)[-1].ticket_ref is None


def test_api_consumption_shows_in_the_ui_history(api, client, part) -> None:
    """Indistinguishable from a movement recorded by hand."""
    _consume(api, part, 2, ticket_ref="26-0001")

    page = client.get(f"/parts/{part.id}").text

    assert "consume" in page
    assert "26-0001" in page
    assert ">8<" in page


def test_consumption_crossing_the_threshold_updates_below_min_immediately(api, part) -> None:
    assert api.get("/api/parts", params={"below_min": "1"}, headers=_auth()).json() == []

    _consume(api, part, 7)  # 10 -> 3, min 4

    low = api.get("/api/parts", params={"below_min": "1"}, headers=_auth()).json()
    assert [p["id"] for p in low] == [part.id]
    assert low[0]["status"] == "low"


def test_consuming_exactly_the_stock_is_fine(api, part) -> None:
    body = _consume(api, part, 10).json()

    assert body["part"]["quantity_on_hand"] == 0
    assert body["part"]["status"] == "out"


def test_session_can_consume_too(client, part, store) -> None:
    """A signed-in browser session reaches the write endpoint (JSON body)."""
    response = client.post(f"/api/parts/{part.id}/consume", json={"quantity": 1})

    assert response.status_code == 200
    assert store.get_part(part.id).quantity_on_hand == 9


# --- rejections: JSON, nothing written --------------------------------------


def test_over_consume_is_400_naming_what_is_on_hand(api, part, store) -> None:
    response = _consume(api, part, 11)

    assert response.status_code == 400
    assert "10" in response.json()["detail"]
    assert store.get_part(part.id).quantity_on_hand == 10
    assert len(store.list_movements(part.id)) == 1


@pytest.mark.parametrize("quantity", [0, -1])
def test_zero_or_negative_quantity_is_400(api, part, store, quantity) -> None:
    response = _consume(api, part, quantity)

    assert response.status_code == 400
    assert store.get_part(part.id).quantity_on_hand == 10


@pytest.mark.parametrize("quantity", ["three", 1.5, None])
def test_non_integer_quantity_is_rejected(api, part, store, quantity) -> None:
    response = api.post(
        f"/api/parts/{part.id}/consume", json={"quantity": quantity}, headers=_auth()
    )

    assert response.status_code in (400, 422)
    assert store.get_part(part.id).quantity_on_hand == 10


def test_missing_quantity_is_422(api, part, store) -> None:
    response = api.post(f"/api/parts/{part.id}/consume", json={"ticket_ref": "x"}, headers=_auth())

    assert response.status_code == 422
    assert store.get_part(part.id).quantity_on_hand == 10


def test_form_encoded_body_is_422(api, part, store) -> None:
    """A JSON body is required. This is also the CSRF story for `/api/`: the
    middleware's token check doesn't cover it, but a cross-site form can only
    send form-encoded / multipart / text-plain — never application/json — so
    it can't ride a browser session into this endpoint."""
    response = api.post(f"/api/parts/{part.id}/consume", data={"quantity": "3"}, headers=_auth())

    assert response.status_code == 422
    assert store.get_part(part.id).quantity_on_hand == 10


def test_unknown_part_is_404(api) -> None:
    response = api.post("/api/parts/9999/consume", json={"quantity": 1}, headers=_auth())

    assert response.status_code == 404


def test_no_auth_is_401_and_writes_nothing(api, part, store) -> None:
    response = api.post(f"/api/parts/{part.id}/consume", json={"quantity": 1})

    assert response.status_code == 401
    assert store.get_part(part.id).quantity_on_hand == 10


def test_a_sequence_keeps_cache_and_ledger_together(api, part, store) -> None:
    _consume(api, part, 3, ticket_ref="26-0001")
    _consume(api, part, 4, ticket_ref="26-0002")
    _consume(api, part, 9)  # rejected

    movements = store.list_movements(part.id)
    assert store.get_part(part.id).quantity_on_hand == sum(m.delta for m in movements) == 3
    assert len(movements) == 3
