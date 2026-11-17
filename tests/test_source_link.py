"""The footer's Source link.

AGPL section 13 obliges whoever runs a modified copy as a network service to
offer its users the source. The stock app carries the offer so a fork inherits
it, which only works if three things stay true: the link is on every page, it's
there *before* sign-in, and a fork can point it at its own code.

Two guards ride along. The footer renders on every page, so it must hold the
link and the licence name and nothing from the inventory. And the link leaves
the site, so it must not have cost anything from the Content-Security-Policy.
"""

import re

import pytest

from bench_parts.app import DEFAULT_SOURCE_URL, LICENSE_NAME, SOURCE_URL_ENV_VAR

FORK_URL = "https://git.example.invalid/someone/parts-fork"


@pytest.fixture(autouse=True)
def _upstream_default(monkeypatch):
    """Start every test from the stock configuration, whatever the shell has set."""
    monkeypatch.delenv(SOURCE_URL_ENV_VAR, raising=False)


@pytest.fixture
def part(store):
    return store.create_part(
        description="Dual N-channel MOSFET",
        category="mosfet",
        mpn="IRF7910",
        supplier="Digi-Key",
        location="A3",
        notes="watch the tab",
    )


def _footer(body: str) -> str:
    match = re.search(r"<footer>(.*?)</footer>", body, re.S)
    assert match, "the base layout has no footer"
    return match.group(1)


def _link(url: str) -> str:
    return f'<a href="{url}" target="_blank" rel="noopener noreferrer">Source</a>'


def test_sign_in_page_offers_the_source_without_a_session(anonymous) -> None:
    response = anonymous.get("/login")

    assert response.status_code == 200
    footer = _footer(response.text)
    assert _link(DEFAULT_SOURCE_URL) in footer
    assert LICENSE_NAME in footer


def test_default_points_at_upstream() -> None:
    assert DEFAULT_SOURCE_URL == "https://github.com/IamMrCupp/bench-parts"
    assert LICENSE_NAME == "AGPL-3.0-or-later"


def test_every_page_carries_the_link(client, part) -> None:
    pages = ("/login", "/", "/order", "/assets", "/activity", "/parts/new", f"/parts/{part.id}")
    for path in pages:
        assert _link(DEFAULT_SOURCE_URL) in _footer(client.get(path).text), path


def test_error_page_carries_the_link(client) -> None:
    response = client.get("/parts/999999")

    assert response.status_code == 404
    assert _link(DEFAULT_SOURCE_URL) in _footer(response.text)


def test_a_fork_can_point_the_link_at_its_own_source(anonymous, monkeypatch) -> None:
    monkeypatch.setenv(SOURCE_URL_ENV_VAR, FORK_URL)

    body = anonymous.get("/login").text

    assert _link(FORK_URL) in _footer(body)
    assert DEFAULT_SOURCE_URL not in body


@pytest.mark.parametrize("value", ["", "   ", "javascript:alert(1)", "example.invalid/fork"])
def test_a_value_that_is_not_a_web_address_falls_back_to_upstream(
    anonymous, monkeypatch, value
) -> None:
    """It lands in an ``href``. A typo must not leave a dead or scripted link."""
    monkeypatch.setenv(SOURCE_URL_ENV_VAR, value)

    assert _link(DEFAULT_SOURCE_URL) in _footer(anonymous.get("/login").text)


def test_footer_holds_the_link_and_the_licence_and_nothing_else(client, part) -> None:
    footer = _footer(client.get(f"/parts/{part.id}").text)

    text = " ".join(re.sub(r"<[^>]+>", " ", footer).split())
    assert text == f"Source · {LICENSE_NAME}"
    assert _link(DEFAULT_SOURCE_URL) in footer


def test_the_link_cost_nothing_from_the_content_security_policy(anonymous) -> None:
    """A link out is navigation, which this policy doesn't govern — so no host
    was added to it to make the footer work."""
    csp = anonymous.get("/login").headers["content-security-policy"]

    assert "github.com" not in csp
    assert "default-src 'self'" in csp
    assert "form-action 'self'" in csp
