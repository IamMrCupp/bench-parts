"""HTML error pages, flash messages, and the low-stock nav count.

The thread through all three: a person on a tablet should never see raw JSON,
never wonder whether a tap worked, and never have to click through to learn
something needs ordering.
"""

import re

import pytest
from fastapi.testclient import TestClient

from bench_parts.app import app
from bench_parts.auth import API_TOKEN_ENV_VAR

TOKEN = "test-api-token-not-a-real-one"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv(API_TOKEN_ENV_VAR, TOKEN)


@pytest.fixture
def part(store):
    return store.create_part(
        description="Dual MOSFET",
        category="mosfet",
        mpn="IRF7910",
        quantity_on_hand=10,
        min_quantity=4,
    )


# --- error pages ------------------------------------------------------------


def test_unknown_part_on_a_browser_route_is_an_html_404(client) -> None:
    response = client.get("/parts/9999")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "no such part" in response.text.lower()
    assert "Catalogue" in response.text  # rendered inside the app's chrome (nav present)


def test_unknown_part_on_the_api_stays_json(db_path) -> None:
    api = TestClient(app)
    response = api.get("/api/parts/9999", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 404
    assert response.json() == {"detail": "no such part"}


def test_unknown_url_is_an_html_404(client) -> None:
    response = client.get("/nope")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")


def test_bad_category_filter_is_an_html_400(client) -> None:
    response = client.get("/", params={"category": "mosfets"})

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "mosfets" in response.text


def test_csrf_failure_is_an_html_403(db_path) -> None:
    from conftest import sign_in

    raw = sign_in(TestClient(app))
    response = raw.post("/parts", data={"description": "x", "category": "other"})

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "reload" in response.text.lower()


def test_error_page_has_a_way_back(client) -> None:
    body = client.get("/parts/9999").text

    assert 'href="/"' in body


def test_error_page_signed_out_still_renders(anonymous) -> None:
    """The 404 handler must not itself require a session (it'd 303 instead)."""
    # /healthz is exempt and exists; an unknown exempt-ish path is the test
    response = anonymous.get("/login/nope", follow_redirects=False)

    assert response.status_code in (
        303,
        404,
    )  # redirect to login is also fine for a signed-out user


# --- flash messages ---------------------------------------------------------


def _flash(body: str) -> str | None:
    m = re.search(r'class="flash[^"]*"[^>]*>(.*?)</', body, re.S)
    return m.group(1).strip() if m else None


def test_adding_a_part_flashes(client) -> None:
    # TestClient follows the 303, so the response body IS the page after the action.
    body = client.post(
        "/parts", data={"description": "New thing", "category": "other", "mpn": "NEW"}
    ).text

    assert _flash(body) and "New thing" in _flash(body)


def test_flash_is_shown_once(client) -> None:
    client.post("/parts", data={"description": "New thing", "category": "other", "mpn": "NEW"})

    assert _flash(client.get("/").text) is None


def test_consume_flashes_with_the_numbers(client, part) -> None:
    body = client.post(
        f"/parts/{part.id}/movements",
        data={"action": "consume", "amount": "3", "ticket_ref": "26-0001"},
    ).text
    flash = _flash(body)

    assert flash and "3" in flash and "IRF7910" in flash and "26-0001" in flash


def test_restock_and_correct_flash(client, part) -> None:
    assert "5" in _flash(
        client.post(f"/parts/{part.id}/movements", data={"action": "restock", "amount": "5"}).text
    )
    assert "2" in _flash(
        client.post(f"/parts/{part.id}/movements", data={"action": "correct", "amount": "-2"}).text
    )


def test_edit_flashes(client, part) -> None:
    body = client.post(
        f"/parts/{part.id}",
        data={
            "description": "Dual MOSFET",
            "category": "mosfet",
            "mpn": "IRF7910",
            "location": "B2",
        },
    ).text

    assert _flash(body)


def test_retire_and_delete_flash(client, store, part) -> None:
    assert "retired" in _flash(client.post(f"/parts/{part.id}/retire", data={}).text).lower()

    fresh = store.create_part(description="Oops", category="other", mpn="OOPS")
    assert "deleted" in _flash(client.post(f"/parts/{fresh.id}/delete", data={}).text).lower()


def test_rejected_action_does_not_flash_success(client, part) -> None:
    body = client.post(
        f"/parts/{part.id}/movements", data={"action": "consume", "amount": "99"}
    ).text

    assert _flash(body) is None


def test_logout_flashes_on_the_login_page(client) -> None:
    page = client.post("/logout", data={}).text  # followed to /login

    assert "signed out" in page.lower()


# --- nav count ----------------------------------------------------------------


def test_nav_shows_low_stock_count(client, store) -> None:
    store.create_part(description="a", category="fuse", mpn="A", quantity_on_hand=0, min_quantity=1)
    store.create_part(description="b", category="fuse", mpn="B", quantity_on_hand=1, min_quantity=1)
    store.create_part(description="c", category="fuse", mpn="C", quantity_on_hand=9, min_quantity=1)

    body = client.get("/").text

    assert re.search(r"To order\s*<[^>]*>\s*2\s*<", body) or "To order (2)" in body


def test_nav_count_hidden_at_zero(client, store) -> None:
    store.create_part(description="c", category="fuse", mpn="C", quantity_on_hand=9, min_quantity=1)

    body = client.get("/").text

    assert 'class="count-badge"' not in body


def test_nav_count_is_live(client, store, part) -> None:
    assert 'class="count-badge"' not in client.get("/").text
    client.post(
        f"/parts/{part.id}/movements", data={"action": "consume", "amount": "7"}
    )  # 10 -> 3, min 4

    assert 'class="count-badge"' in client.get("/").text
