"""The index page renders through the template layer.

These assert the *render path* — templates resolving, inheritance working, the
viewport tag surviving to the output — not the placeholder copy. Phase 2 can
rewrite the page's content without rewriting these tests.
"""

from fastapi.testclient import TestClient

from bench_parts.app import app

client = TestClient(app)


def test_index_renders_html() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_index_carries_viewport_tag() -> None:
    # The client is a tablet; without this tag mobile browsers render at desktop
    # width. It lives in the base layout, so its presence proves inheritance
    # resolved rather than a stray literal in the child template.
    body = client.get("/").text

    assert 'name="viewport"' in body
