"""Login, logout, the session cookie, and login throttling.

Two tests here are about not leaking information rather than about function:
`test_wrong_password_message_is_unhelpful` and the open-redirect guard. Both
are cheap now and awkward to retrofit once someone relies on the behaviour.
"""

import re

import pytest
from fastapi.testclient import TestClient

from bench_parts.app import app, login_limiter
from conftest import TEST_PASSWORD

_TOKEN_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


@pytest.fixture
def raw(db_path):
    """A signed-out client that still does the browser's CSRF job by hand."""
    return TestClient(app)


def _login(client, password=TEST_PASSWORD, **extra):
    token = _TOKEN_RE.search(client.get("/login").text).group(1)
    return client.post(
        "/login", data={"password": password, "csrf_token": token, **extra}, follow_redirects=False
    )


# --- the login page ---------------------------------------------------------


def test_login_page_renders(raw) -> None:
    response = raw.get("/login")

    assert response.status_code == 200
    assert 'name="password"' in response.text


def test_login_page_uses_a_password_field(raw) -> None:
    """Not `type="text"` — shoulder-surfing at a bench is the whole point."""
    assert 'type="password"' in raw.get("/login").text


# --- logging in -------------------------------------------------------------


def test_correct_password_redirects_to_the_catalogue(raw) -> None:
    response = _login(raw)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_correct_password_sets_a_session_cookie(raw) -> None:
    _login(raw)

    assert raw.cookies.get("session")


def test_session_persists_across_requests(raw) -> None:
    _login(raw)

    assert raw.get("/", follow_redirects=False).status_code == 200


def test_wrong_password_is_401(raw) -> None:
    assert _login(raw, password="not the password").status_code == 401


def test_wrong_password_grants_no_session(raw) -> None:
    """The cookie may exist (it carries the CSRF token) — what must not happen
    is the session being marked authenticated. Asserted by reachability."""
    _login(raw, password="not the password")

    assert raw.get("/", follow_redirects=False).status_code == 303


def test_empty_password_is_rejected(raw) -> None:
    assert _login(raw, password="").status_code == 401


def test_wrong_password_message_is_unhelpful(raw) -> None:
    """Must not distinguish 'wrong' from 'none configured', nor hint at format."""
    body = _login(raw, password="wrong").text.lower()

    for leak in ["not set", "not configured", "too short", "length", "hash", "scrypt"]:
        assert leak not in body


# --- logging out ------------------------------------------------------------


def test_logout_ends_the_session_and_re_locks(client) -> None:
    assert client.get("/", follow_redirects=False).status_code == 200

    response = client.post("/logout", data={}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303


def test_sign_out_button_is_shown_when_signed_in(client, anonymous) -> None:
    assert 'action="/logout"' in client.get("/").text
    assert 'action="/logout"' not in anonymous.get("/login").text


# --- ?next= -----------------------------------------------------------------


def test_next_is_honoured(raw) -> None:
    """A deep link into a part must survive the login round-trip."""
    assert _login(raw, next="/parts/7").headers["location"] == "/parts/7"


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example.com/phish",
        "//evil.example.com",
        "http://evil.example.com",
        "javascript:alert(1)",
        "\\\\evil.example.com",
        "/\\evil.example.com",
    ],
)
def test_offsite_next_is_ignored(raw, hostile) -> None:
    """Open-redirect guard: a login link is exactly what gets pasted into a
    message, and bouncing to an attacker's page after a real login is how
    credentials get phished."""
    assert _login(raw, next=hostile).headers["location"] == "/", f"open redirect via {hostile!r}"


def test_redirect_to_login_preserves_the_requested_path(anonymous) -> None:
    location = anonymous.get("/parts/7", follow_redirects=False).headers["location"]

    assert location.startswith("/login?next=")
    assert "/parts/7" in location


def test_deep_link_survives_the_login(raw) -> None:
    location = raw.get("/order", follow_redirects=False).headers["location"]
    next_target = location.split("next=")[1]

    assert _login(raw, next=next_target).headers["location"] == "/order"


# --- cookie flags -----------------------------------------------------------


def test_cookie_is_httponly_and_samesite_lax(raw) -> None:
    header = _login(raw).headers.get("set-cookie", "").lower()

    assert "httponly" in header
    assert "samesite=lax" in header


def test_cookie_is_not_secure_by_default(raw) -> None:
    """LAN-only over plain HTTP by design. A `secure` cookie would never be
    sent, so login would appear to work and then silently fail."""
    assert "secure" not in _login(raw).headers.get("set-cookie", "").lower()


# --- throttling -------------------------------------------------------------


def test_repeated_failures_get_throttled(raw) -> None:
    for _ in range(login_limiter.max_attempts):
        assert _login(raw, password="wrong").status_code == 401

    response = _login(raw, password="wrong")

    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


def test_lockout_blocks_even_the_correct_password(raw) -> None:
    """Otherwise a lockout is just a speed bump for the guess that lands."""
    for _ in range(login_limiter.max_attempts):
        _login(raw, password="wrong")

    assert _login(raw).status_code == 429


def test_lockout_message_says_how_long(raw) -> None:
    for _ in range(login_limiter.max_attempts):
        _login(raw, password="wrong")

    assert "minute" in _login(raw, password="wrong").text.lower()


def test_success_before_the_limit_clears_the_count(raw) -> None:
    for _ in range(login_limiter.max_attempts - 1):
        _login(raw, password="wrong")
    assert _login(raw).status_code == 303  # signs in

    raw.cookies.clear()
    for _ in range(login_limiter.max_attempts - 1):
        assert _login(raw, password="wrong").status_code == 401
