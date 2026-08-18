"""The session-secret, cookie, API-token and CSRF helpers.

Everything here fails *closed*: an unset token env var, a missing CSRF token,
a session with no secret — each must deny, never be treated as "nothing to
check". These are the cheap-to-write, awkward-to-retrofit guarantees.
"""

import pytest

from bench_parts import auth


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv(auth.ENV_VAR, auth.hash_password("bench password"))
    monkeypatch.delenv(auth.SESSION_ENV_VAR, raising=False)
    monkeypatch.delenv(auth.COOKIE_SECURE_ENV_VAR, raising=False)
    monkeypatch.delenv(auth.API_TOKEN_ENV_VAR, raising=False)


# --- session secret ---------------------------------------------------------


def test_session_secret_derives_from_the_hash_by_default() -> None:
    assert auth.session_secret()
    assert auth.session_secret() == auth.session_secret()  # stable across calls


def test_session_secret_is_not_the_hash_itself() -> None:
    assert auth.session_secret() != auth.configured_hash()


def test_explicit_session_secret_wins(monkeypatch) -> None:
    monkeypatch.setenv(auth.SESSION_ENV_VAR, "explicit")

    assert auth.session_secret() == "explicit"


def test_rotating_the_password_rotates_the_secret(monkeypatch) -> None:
    """Which logs everyone out — the behaviour you'd want anyway."""
    before = auth.session_secret()
    monkeypatch.setenv(auth.ENV_VAR, auth.hash_password("new password"))

    assert auth.session_secret() != before


# --- cookie flag ------------------------------------------------------------


def test_cookie_is_not_secure_by_default() -> None:
    assert auth.cookie_secure() is False


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_cookie_secure_can_be_enabled(monkeypatch, value) -> None:
    monkeypatch.setenv(auth.COOKIE_SECURE_ENV_VAR, value)

    assert auth.cookie_secure() is True


@pytest.mark.parametrize("value", ["", "0"])
def test_cookie_secure_off_values(monkeypatch, value) -> None:
    monkeypatch.setenv(auth.COOKIE_SECURE_ENV_VAR, value)

    assert auth.cookie_secure() is False


# --- API token --------------------------------------------------------------


def test_no_configured_token_rejects_everything() -> None:
    """Fails closed: an unset token must never mean 'any token works'."""
    assert auth.valid_api_token("Bearer anything") is False


def test_matching_bearer_token_is_accepted(monkeypatch) -> None:
    monkeypatch.setenv(auth.API_TOKEN_ENV_VAR, "s3cret")

    assert auth.valid_api_token("Bearer s3cret") is True


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Bearer wrong", "Basic s3cret", "s3cret", "bearer s3cretx"],
)
def test_wrong_or_malformed_bearer_is_rejected(monkeypatch, header) -> None:
    monkeypatch.setenv(auth.API_TOKEN_ENV_VAR, "s3cret")

    assert auth.valid_api_token(header) is False


def test_bearer_scheme_is_case_insensitive(monkeypatch) -> None:
    monkeypatch.setenv(auth.API_TOKEN_ENV_VAR, "s3cret")

    assert auth.valid_api_token("bearer s3cret") is True


# --- CSRF -------------------------------------------------------------------


def test_csrf_token_is_minted_once_per_session() -> None:
    session: dict = {}

    first = auth.csrf_token(session)
    second = auth.csrf_token(session)

    assert first == second
    assert len(first) >= 32


def test_csrf_tokens_differ_between_sessions() -> None:
    assert auth.csrf_token({}) != auth.csrf_token({})


def test_csrf_valid_round_trip() -> None:
    session: dict = {}
    token = auth.csrf_token(session)

    assert auth.csrf_valid(session, token) is True


@pytest.mark.parametrize("submitted", [None, "", "wrong"])
def test_csrf_missing_or_wrong_fails_closed(submitted) -> None:
    session: dict = {}
    auth.csrf_token(session)

    assert auth.csrf_valid(session, submitted) is False


def test_csrf_valid_with_no_session_token_fails_closed() -> None:
    """A session that never minted a token must not accept any submission."""
    assert auth.csrf_valid({}, "anything") is False


def test_csrf_token_from_another_session_is_rejected() -> None:
    a: dict = {}
    b: dict = {}
    foreign = auth.csrf_token(b)
    auth.csrf_token(a)

    assert auth.csrf_valid(a, foreign) is False
