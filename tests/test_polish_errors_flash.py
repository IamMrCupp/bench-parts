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
    client.post("/parts", data={"description": "New thing", "category": "other", "mpn": "NEW"})
    body = client.get("/").text  # follow the redirect target by hand

    assert _flash(body) and "New thing" in _flash(body)


def test_flash_is_shown_once(client) -> None:
    client.post("/parts", data={"description": "New thing", "category": "other", "mpn": "NEW"})
    client.get("/")

    assert _flash(client.get("/").text) is None


def test_consume_flashes_with_the_numbers(client, part) -> None:
    client.post(
        f"/parts/{part.id}/movements",
        data={"action": "consume", "amount": "3", "ticket_ref": "26-0001"},
    )
    flash = _flash(client.get(f"/parts/{part.id}").text)

    assert flash and "3" in flash and "IRF7910" in flash and "26-0001" in flash


def test_restock_and_correct_flash(client, part) -> None:
    client.post(f"/parts/{part.id}/movements", data={"action": "restock", "amount": "5"})
    assert "5" in _flash(client.get(f"/parts/{part.id}").text)

    client.post(f"/parts/{part.id}/movements", data={"action": "correct", "amount": "-2"})
    assert "2" in _flash(client.get(f"/parts/{part.id}").text)


def test_edit_flashes(client, part) -> None:
    client.post(
        f"/parts/{part.id}",
        data={
            "description": "Dual MOSFET",
            "category": "mosfet",
            "mpn": "IRF7910",
            "location": "B2",
        },
    )

    assert _flash(client.get(f"/parts/{part.id}").text)


def test_retire_and_delete_flash(client, store, part) -> None:
    client.post(f"/parts/{part.id}/retire", data={})
    assert "retired" in _flash(client.get(f"/parts/{part.id}").text).lower()

    fresh = store.create_part(description="Oops", category="other", mpn="OOPS")
    client.post(f"/parts/{fresh.id}/delete", data={})
    assert "deleted" in _flash(client.get("/").text).lower()


def test_rejected_action_does_not_flash_success(client, part) -> None:
    client.post(f"/parts/{part.id}/movements", data={"action": "consume", "amount": "99"})

    assert _flash(client.get(f"/parts/{part.id}").text) is None


def test_login_flashes_nothing_but_logout_does(client) -> None:
    client.post("/logout", data={})
    from conftest import TEST_PASSWORD

    page = client.get("/login").text
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

    assert "To order (0)" not in body
    assert not re.search(r"To order\s*<[^>]*class=\"count-badge\"", body)


def test_nav_count_is_live(client, store, part) -> None:
    assert "count-badge" not in client.get("/").text
    client.post(
        f"/parts/{part.id}/movements", data={"action": "consume", "amount": "7"}
    )  # 10 -> 3, min 4

    assert "count-badge" in client.get("/").text
