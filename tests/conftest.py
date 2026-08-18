"""Shared fixtures for the web-layer tests.

Two things happen here that a new test file needs to know about:

1. **The auth env is configured at import time.** The app resolves its session
   secret when ``bench_parts.app`` is imported, and raises without a password
   hash. That's deliberate — a misconfigured deployment should crashloop
   visibly rather than start serving the inventory wide open — but it means the
   test suite has to configure itself the way a deployment does, *before* any
   test module imports the app. Setting it here, once, means no future test
   file can accidentally import the app unconfigured and get a confusing error
   a long way from the cause.

2. **The ``client`` fixture is signed in and carries its CSRF token.** Every
   route except ``/healthz`` and ``/login`` needs a session, and every POST
   needs a token. Tests about page behaviour shouldn't each re-implement
   "log in, read the hidden field, post it back" — that's browser mechanics,
   not the thing under test. Tests *about* the gate or CSRF use a plain
   ``TestClient`` so they can omit or corrupt things deliberately.

The app resolves its database path from ``BENCH_PARTS_DB_PATH`` on every
request, so pointing that at a per-test temp file gives each test a clean,
isolated database — and the same path can be opened directly for seeding or
asserting, without going through HTTP.
"""

import os
import re

import pytest
from fastapi.testclient import TestClient

from bench_parts.auth import COOKIE_SECURE_ENV_VAR, ENV_VAR, SESSION_ENV_VAR, hash_password
from bench_parts.db import connect
from bench_parts.schema import initialise
from bench_parts.store import PartStore

TEST_PASSWORD = "bench password"

# Hashed once at collection: scrypt is deliberately not cheap, and this runs
# before every test module that touches the app.
os.environ.setdefault(ENV_VAR, hash_password(TEST_PASSWORD))
os.environ.setdefault(SESSION_ENV_VAR, "test-session-secret-not-a-real-one")
os.environ.setdefault(COOKIE_SECURE_ENV_VAR, "0")

_TOKEN_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


class CsrfClient(TestClient):
    """A client that carries its CSRF token, the way a browser does.

    An explicitly supplied ``csrf_token`` always wins, so a test can still pass
    a deliberately wrong one through this client.
    """

    def post(self, url, *, data=None, files=None, **kwargs):
        # A JSON post (the API) carries no form and needs no token — the
        # middleware doesn't CSRF-check /api/, and adding a form field would
        # turn the request into multipart and 422 it.
        if "json" in kwargs:
            return super().post(url, data=data, files=files, **kwargs)
        if not (isinstance(data, dict) and "csrf_token" in data):
            data = {**(data or {}), "csrf_token": self._csrf()}
        return super().post(url, data=data, files=files, **kwargs)

    def _csrf(self) -> str:
        for page in ("/", "/login"):
            match = _TOKEN_RE.search(self.get(page).text)
            if match:
                return match.group(1)
        raise AssertionError("could not find a CSRF token on any accessible page")


def sign_in(client):
    """Give a test client a session.

    ``/login`` is exempt from *auth* but not from CSRF, so the token has to come
    from the rendered form — exactly as a browser would do it.
    """
    match = _TOKEN_RE.search(client.get("/login").text)
    assert match, "login form is missing its CSRF token"

    response = client.post(
        "/login",
        data={"password": TEST_PASSWORD, "csrf_token": match.group(1)},
        follow_redirects=False,
    )
    assert response.status_code == 303, f"test sign-in failed: {response.status_code}"
    return client


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """A fresh database file, wired into the app via the env var."""
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    path = tmp_path / "parts.db"
    monkeypatch.setenv("BENCH_PARTS_DB_PATH", str(path))
    with connect(path) as conn:
        initialise(conn)
    return path


@pytest.fixture
def store(db_path):
    """Direct store access to the same database the app is serving."""
    with connect(db_path) as conn:
        yield PartStore(conn)


@pytest.fixture
def client(db_path):
    """A signed-in, CSRF-carrying client against the app, database wired."""
    from bench_parts.app import app

    return sign_in(CsrfClient(app))


@pytest.fixture
def anonymous(db_path):
    """A plain client with no session and no token — for tests about the gate."""
    from bench_parts.app import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_login_throttle():
    """Give every test a clean rate-limit bucket.

    The limiter is process-global and ``TestClient`` always reports the same
    host, so without this every wrong-password test in the suite shares one
    counter — and the fifth such test added anywhere would start failing tests
    that have nothing to do with it.
    """
    from bench_parts.app import login_limiter

    login_limiter._failures.clear()
    yield
    login_limiter._failures.clear()
