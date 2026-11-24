"""Behind a reverse proxy (#151).

``serve`` hands ``BENCH_PARTS_TRUSTED_PROXIES`` to uvicorn, and uvicorn wraps the
app in its ``ProxyHeadersMiddleware``. These tests wrap the app the same way and
connect from a chosen peer address, so they exercise what a browser behind the
ingress gets: its own login throttle bucket, and ``https`` in the URLs the API
builds. From a peer that isn't listed, the forwarded headers must do nothing.

What ``serve`` passes to uvicorn, and what it refuses, is in ``test_cli.py``.
"""

import re

import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from bench_parts.app import app, login_limiter
from bench_parts.auth import API_TOKEN_ENV_VAR

_TOKEN_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')

TRUSTED_RANGE = "10.42.0.0/16"
PROXY = ("10.42.1.2", 50000)  # the ingress controller's pod
STRANGER = ("192.168.7.99", 50000)  # reaches the pod without going through it
API_TOKEN = "test-api-token-not-a-real-one"


def _behind(peer):
    """The app as uvicorn serves it with the setting on, reached from ``peer``."""
    return TestClient(ProxyHeadersMiddleware(app, trusted_hosts=TRUSTED_RANGE), client=peer)


@pytest.fixture
def via_proxy(db_path):
    return _behind(PROXY)


@pytest.fixture
def direct(db_path):
    return _behind(STRANGER)


def _wrong_password(client, forwarded_for=None):
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
    token = _TOKEN_RE.search(client.get("/login", headers=headers).text).group(1)
    return client.post(
        "/login",
        data={"password": "wrong", "csrf_token": token},
        headers=headers,
        follow_redirects=False,
    )


# --- the throttle bucket ------------------------------------------------------


def test_a_lockout_for_one_forwarded_address_does_not_lock_another(via_proxy) -> None:
    for _ in range(login_limiter.max_attempts):
        assert _wrong_password(via_proxy, "192.168.7.50").status_code == 401

    assert _wrong_password(via_proxy, "192.168.7.50").status_code == 429
    assert _wrong_password(via_proxy, "192.168.7.51").status_code == 401


def test_the_proxy_itself_is_not_the_bucket(via_proxy) -> None:
    for _ in range(login_limiter.max_attempts):
        _wrong_password(via_proxy, "192.168.7.50")

    assert not login_limiter.allowed("192.168.7.50")
    assert login_limiter.allowed(PROXY[0])


def test_a_forwarded_header_from_an_unlisted_peer_is_ignored(direct) -> None:
    """Changing the header buys nothing: every attempt lands in the peer's bucket."""
    for n in range(login_limiter.max_attempts):
        assert _wrong_password(direct, f"192.168.7.{50 + n}").status_code == 401

    assert _wrong_password(direct, "192.168.7.200").status_code == 429
    assert not login_limiter.allowed(STRANGER[0])
    assert login_limiter.allowed("192.168.7.50")


def test_a_client_cannot_forge_its_way_past_the_proxy(via_proxy) -> None:
    """nginx appends the address it saw; uvicorn reads from the right and stops
    at the first hop that isn't trusted, so a made-up leftmost entry is unused."""
    for n in range(login_limiter.max_attempts):
        _wrong_password(via_proxy, f"203.0.113.{n}, 192.168.7.50")

    assert _wrong_password(via_proxy, "203.0.113.99, 192.168.7.50").status_code == 429


def test_without_the_middleware_the_header_is_ignored(db_path) -> None:
    """The app never reads ``X-Forwarded-For`` itself, whoever the peer is."""
    bare = TestClient(app, client=PROXY)
    for n in range(login_limiter.max_attempts):
        assert _wrong_password(bare, f"192.168.7.{50 + n}").status_code == 401

    assert _wrong_password(bare, "192.168.7.200").status_code == 429
    assert not login_limiter.allowed(PROXY[0])


# --- the scheme ---------------------------------------------------------------


@pytest.fixture
def part(store, monkeypatch):
    monkeypatch.setenv(API_TOKEN_ENV_VAR, API_TOKEN)
    return store.create_part(description="N-MOSFET", category="mosfet")


def _part_url(client, part, **headers):
    response = client.get(
        f"/api/parts/{part.id}", headers={"Authorization": f"Bearer {API_TOKEN}", **headers}
    )
    assert response.status_code == 200
    return response.json()["url"]


def test_forwarded_proto_from_the_proxy_makes_api_urls_https(via_proxy, part) -> None:
    url = _part_url(via_proxy, part, **{"X-Forwarded-Proto": "https"})

    assert url.startswith("https://")
    assert url.endswith(f"/parts/{part.id}")


def test_forwarded_proto_from_an_unlisted_peer_is_ignored(direct, part) -> None:
    assert _part_url(direct, part, **{"X-Forwarded-Proto": "https"}).startswith("http://")


def test_without_forwarded_proto_the_scheme_is_unchanged(via_proxy, part) -> None:
    assert _part_url(via_proxy, part).startswith("http://")
