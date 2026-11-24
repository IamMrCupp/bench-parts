"""Behind a reverse proxy (#151).

``serve`` and the tray app hand ``BENCH_PARTS_TRUSTED_PROXIES`` to uvicorn
(``bench_parts.proxies``), and uvicorn wraps the app in its
``ProxyHeadersMiddleware``. Three layers of test:

- the app behind that middleware, reached from a chosen peer address: what a
  browser behind the ingress gets, which is its own login throttle bucket and
  ``https`` in the URLs the API builds;
- ``uvicorn.Config`` loaded with the real options: the middleware is there only
  when the setting is;
- the real server over real HTTP on loopback: unset, even ``127.0.0.1`` is not
  believed.

What ``serve`` passes to uvicorn, and what it refuses, is in ``test_cli.py``.
"""

import json
import re
import urllib.request

import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from bench_parts import proxies
from bench_parts.app import app, login_limiter
from bench_parts.auth import API_TOKEN_ENV_VAR
from bench_parts.proxies import TRUSTED_PROXIES_ENV_VAR
from bench_parts.server import ServerController

_TOKEN_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')

TRUSTED_RANGE = "10.42.0.0/16"
PROXY = ("10.42.1.2", 50000)  # the ingress controller's pod
STRANGER = ("192.168.7.99", 50000)  # reaches the pod without going through it
API_TOKEN = "test-api-token-not-a-real-one"


@pytest.fixture(autouse=True)
def _no_setting(monkeypatch):
    monkeypatch.delenv(TRUSTED_PROXIES_ENV_VAR, raising=False)


def _behind(peer, monkeypatch):
    """The app as uvicorn serves it with the setting on, reached from ``peer``:
    the list goes through the same parser, into the same middleware."""
    monkeypatch.setenv(TRUSTED_PROXIES_ENV_VAR, TRUSTED_RANGE)
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=proxies.trusted_proxies())
    return TestClient(wrapped, client=peer)


@pytest.fixture
def via_proxy(db_path, monkeypatch):
    return _behind(PROXY, monkeypatch)


@pytest.fixture
def direct(db_path, monkeypatch):
    return _behind(STRANGER, monkeypatch)


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


# --- what uvicorn is configured with ------------------------------------------


def _loaded(monkeypatch, value):
    import uvicorn

    if value is not None:
        monkeypatch.setenv(TRUSTED_PROXIES_ENV_VAR, value)
    config = uvicorn.Config(app, **proxies.server_options())
    config.load()
    return config.loaded_app


@pytest.mark.parametrize("value", [None, "", " , "])
def test_unset_uvicorn_gets_the_app_without_the_middleware(monkeypatch, value) -> None:
    assert not isinstance(_loaded(monkeypatch, value), ProxyHeadersMiddleware)


def test_unset_ignores_uvicorns_own_environment_variable(monkeypatch) -> None:
    """``FORWARDED_ALLOW_IPS`` is uvicorn's own way in. It must not be a second one."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    assert not isinstance(_loaded(monkeypatch, None), ProxyHeadersMiddleware)


def test_set_uvicorn_wraps_the_app_and_trusts_only_the_list(monkeypatch) -> None:
    loaded = _loaded(monkeypatch, TRUSTED_RANGE)

    assert isinstance(loaded, ProxyHeadersMiddleware)
    assert PROXY[0] in loaded.trusted_hosts
    assert STRANGER[0] not in loaded.trusted_hosts
    assert "127.0.0.1" not in loaded.trusted_hosts


# --- the real server, over real HTTP on loopback ---------------------------------


async def _who_am_i(scope, receive, send):
    """A tiny ASGI app: who the server says the client is, and on what scheme."""
    if scope["type"] != "http":
        return
    body = json.dumps({"client": scope["client"][0], "scheme": scope["scheme"]}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _seen_by_the_server() -> dict[str, str]:
    """Start the server the way the tray app does, and ask it who we are while
    claiming to be a tablet on HTTPS."""
    controller = ServerController(app_factory=lambda: _who_am_i)
    state = controller.start("127.0.0.1", 0)
    try:
        assert state.running, state.error
        request = urllib.request.Request(
            state.url,
            headers={"X-Forwarded-For": "192.168.7.50", "X-Forwarded-Proto": "https"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)
    finally:
        controller.stop()


def test_unset_loopback_is_not_believed() -> None:
    """The gap this closes: uvicorn alone trusts ``127.0.0.1``, and on a
    one-machine install every browser is ``127.0.0.1``."""
    assert _seen_by_the_server() == {"client": "127.0.0.1", "scheme": "http"}


def test_loopback_is_not_believed_when_only_another_range_is_listed(monkeypatch) -> None:
    monkeypatch.setenv(TRUSTED_PROXIES_ENV_VAR, TRUSTED_RANGE)
    assert _seen_by_the_server() == {"client": "127.0.0.1", "scheme": "http"}


def test_loopback_is_believed_once_it_is_listed(monkeypatch) -> None:
    """A proxy on the same machine is listed like any other."""
    monkeypatch.setenv(TRUSTED_PROXIES_ENV_VAR, "127.0.0.1")
    assert _seen_by_the_server() == {"client": "192.168.7.50", "scheme": "https"}


@pytest.mark.parametrize("value", ["*", "0.0.0.0/0", "::/0", "ingress-nginx"])
def test_the_tray_server_will_not_start_on_a_bad_list(monkeypatch, value) -> None:
    monkeypatch.setenv(TRUSTED_PROXIES_ENV_VAR, value)
    started = []
    controller = ServerController(app_factory=lambda: started.append(1) or _who_am_i)

    state = controller.start("127.0.0.1", 0)

    assert not state.running
    assert TRUSTED_PROXIES_ENV_VAR in state.error
    assert started == []
    assert not controller.state.running
