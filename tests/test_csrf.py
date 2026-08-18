"""CSRF protection on mutating routes.

`test_every_form_in_the_app_carries_a_token` is the durable one: it crawls the
app's own pages and checks each `<form method=post>` rather than asserting
against a list someone has to maintain. A form added later should fail this if
it ships without a token, without anyone extending a checklist.
"""

import re

import pytest
from fastapi.testclient import TestClient

from bench_parts.app import app
from conftest import CsrfClient, sign_in

_FORM_RE = re.compile(r"<form[^>]*method=[\"']post[\"'][^>]*>.*?</form>", re.S | re.I)


@pytest.fixture
def part(store):
    return store.create_part(description="x", category="mosfet", mpn="IRF7910", quantity_on_hand=5)


def _raw_signed_in(db_path):
    """Signed in, but a plain client — so posts carry no token unless we add one."""
    return sign_in(TestClient(app))


# --- refusal ----------------------------------------------------------------


def test_post_without_a_token_is_403(db_path, store, part) -> None:
    raw = _raw_signed_in(db_path)

    response = raw.post(f"/parts/{part.id}/movements", data={"action": "restock", "amount": "5"})

    assert response.status_code == 403
    assert store.get_part(part.id).quantity_on_hand == 5


def test_post_with_a_garbage_token_is_403(db_path, store, part) -> None:
    raw = _raw_signed_in(db_path)

    response = raw.post(
        f"/parts/{part.id}/movements",
        data={"action": "restock", "amount": "5", "csrf_token": "not-a-real-token"},
    )

    assert response.status_code == 403
    assert store.get_part(part.id).quantity_on_hand == 5


def test_refused_post_writes_nothing(db_path, store) -> None:
    """The point of the whole exercise: a forged request must not create a
    part, not merely be reported as failed."""
    raw = _raw_signed_in(db_path)

    raw.post("/parts", data={"description": "Forged", "category": "other"})

    assert store.list_parts() == []


def test_a_token_from_another_session_is_refused(db_path, store, part) -> None:
    """Tokens are bound to a session, so a stolen one from elsewhere is useless."""
    victim = _raw_signed_in(db_path)
    attacker = sign_in(CsrfClient(app))
    foreign = attacker._csrf()

    response = victim.post(
        f"/parts/{part.id}/movements",
        data={"action": "restock", "amount": "5", "csrf_token": foreign},
    )

    assert response.status_code == 403
    assert store.get_part(part.id).quantity_on_hand == 5


def test_login_itself_requires_a_token(anonymous) -> None:
    """An attacker logging a victim in is a real attack shape, and exempting
    login would mean the rule has an exception to remember."""
    response = anonymous.post("/login", data={"password": "bench password"})

    assert response.status_code == 403


def test_logout_requires_a_token(db_path) -> None:
    raw = _raw_signed_in(db_path)

    assert raw.post("/logout", data={}).status_code == 403
    assert raw.get("/", follow_redirects=False).status_code == 200  # still signed in


# --- acceptance -------------------------------------------------------------


def test_post_with_a_valid_token_works(client, store, part) -> None:
    response = client.post(
        f"/parts/{part.id}/movements",
        data={"action": "restock", "amount": "5"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert store.get_part(part.id).quantity_on_hand == 10


@pytest.mark.parametrize("page", ["/parts/new", "/login"])
def test_every_form_in_the_app_carries_a_token(client, page) -> None:
    """Crawl rendered pages rather than checking against a maintained list."""
    body = client.get(page).text

    forms = _FORM_RE.findall(body)
    assert forms, f"expected at least one POST form on {page}"
    for form in forms:
        action = re.search(r'action="([^"]*)"', form)
        assert 'name="csrf_token"' in form, (
            f"form action={action.group(1) if action else '?'} on {page} has no CSRF token"
        )


def test_part_detail_forms_all_carry_a_token(client, part) -> None:
    """The busiest page: edit + three stock controls + sign-out."""
    forms = _FORM_RE.findall(client.get(f"/parts/{part.id}").text)

    assert len(forms) >= 5, f"expected edit, restock, use, correct, sign-out — found {len(forms)}"
    for form in forms:
        assert 'name="csrf_token"' in form


def test_get_requests_need_no_token(client) -> None:
    """Reads are unaffected — a token requirement on GET would break links."""
    for path in ["/", "/order", "/parts/new"]:
        assert client.get(path).status_code == 200


def test_csrf_is_not_authentication(anonymous, store) -> None:
    """A valid-looking token must not substitute for a session."""
    response = anonymous.post(
        "/parts",
        data={"description": "x", "category": "other", "csrf_token": "anything"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert store.list_parts() == []
