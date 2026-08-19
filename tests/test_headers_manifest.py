"""Security headers, session lifetime, and installability (icons + manifest)."""

import re

import pytest

from bench_parts.app import EXEMPT_PATHS, EXEMPT_PREFIXES

# --- headers ----------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/login", "/healthz", "/parts/new"])
def test_security_headers_on_every_response(client, path) -> None:
    h = client.get(path).headers

    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "same-origin"
    assert "camera=()" in h["permissions-policy"]
    csp = h["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp.split("style-src")[0]  # only styles get it
    assert "style-src 'self' 'unsafe-inline'" in csp


def test_headers_on_api_and_error_responses_too(anonymous) -> None:
    assert anonymous.get("/api/parts").headers["x-frame-options"] == "DENY"  # 401
    assert anonymous.get("/healthz").headers["content-security-policy"]


def test_no_inline_script_anywhere(client, store) -> None:
    """CSP says script-src 'self' — so the templates must not carry inline JS,
    or the confirm() dialogs silently stop working."""
    p = store.create_part(description="x", category="other", mpn="X")
    for path in ["/", "/order", "/parts/new", f"/parts/{p.id}", "/login"]:
        body = client.get(path).text
        assert "onsubmit=" not in body, path
        assert "onclick=" not in body, path
        assert not re.search(r"<script(?![^>]*\bsrc=)", body), path


def test_confirm_handlers_moved_to_static_js(client, store) -> None:
    p = store.create_part(description="x", category="other", mpn="X")
    body = client.get(f"/parts/{p.id}").text

    assert 'data-confirm="' in body
    assert '<script src="/static/app.js"' in body
    js = client.get("/static/app.js").text
    assert "data-confirm" in js and "confirm(" in js


# --- session lifetime -------------------------------------------------------


def test_session_cookie_has_a_30_day_max_age(anonymous) -> None:
    from conftest import TEST_PASSWORD

    tok = re.search(r'name="csrf_token"\s+value="([^"]+)"', anonymous.get("/login").text).group(1)
    r = anonymous.post(
        "/login", data={"password": TEST_PASSWORD, "csrf_token": tok}, follow_redirects=False
    )

    assert "max-age=2592000" in r.headers["set-cookie"].lower()


# --- icons + manifest -------------------------------------------------------


def test_static_assets_are_served_signed_out(anonymous) -> None:
    """The browser fetches icons and the manifest without a session; a redirect
    to /login there means no icon and a broken install."""
    for path, ctype in [
        ("/static/favicon.svg", "image/svg+xml"),
        ("/static/app.js", "javascript"),
        ("/manifest.webmanifest", "manifest+json"),
    ]:
        r = anonymous.get(path, follow_redirects=False)
        assert r.status_code == 200, path
        assert ctype in r.headers["content-type"], (path, r.headers["content-type"])


def test_manifest_describes_a_standalone_app(anonymous) -> None:
    m = anonymous.get("/manifest.webmanifest").json()

    assert m["name"] == "bench-parts"
    assert m["display"] == "standalone"
    assert m["start_url"] == "/"
    assert any(i["src"].endswith(".svg") for i in m["icons"])
    assert any(i["src"].endswith(".png") for i in m["icons"])
    assert m["theme_color"].startswith("#")


def test_head_links_icon_manifest_and_theme(client) -> None:
    body = client.get("/").text

    assert 'rel="icon"' in body and "favicon.svg" in body
    assert 'rel="manifest"' in body and "manifest.webmanifest" in body
    assert 'rel="apple-touch-icon"' in body
    assert 'name="theme-color"' in body
    assert 'name="apple-mobile-web-app-capable" content="yes"' in body


def test_png_icon_is_a_real_png(anonymous) -> None:
    r = anonymous.get("/static/icon-192.png")

    assert r.status_code == 200
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_static_is_exempt_by_prefix_not_listed_per_file(anonymous) -> None:
    """The exemption is a prefix — a new icon doesn't need a new exempt entry,
    but it's still a single, visible hole, not per-route decorators."""
    assert "/static/" in EXEMPT_PREFIXES
    assert "/manifest.webmanifest" in EXEMPT_PATHS


def test_everything_else_is_still_gated(anonymous) -> None:
    assert anonymous.get("/", follow_redirects=False).status_code == 303
    assert anonymous.get("/static/../parts/1", follow_redirects=False).status_code in (303, 404)
