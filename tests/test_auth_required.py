"""Route protection.

`test_every_route_is_protected_or_explicitly_exempt` is the real deliverable
here. The middleware itself is a dozen lines; what's hard is guaranteeing that
a route added in Phase 4 — the JSON API — can't quietly ship unprotected. That
test enumerates the app's own routing table, so it fails on a new unlisted
route rather than trusting anyone to remember.
"""

import pytest
from fastapi.testclient import TestClient

from bench_parts.app import EXEMPT_PATHS, app


@pytest.fixture
def part(store):
    return store.create_part(description="x", category="mosfet", mpn="IRF7910", quantity_on_hand=5)


# --- the gate ---------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/order", "/parts/new", "/parts/1"])
def test_get_routes_redirect_when_signed_out(anonymous, path) -> None:
    response = anonymous.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize("path", ["/parts", "/parts/1", "/parts/1/movements"])
def test_post_routes_are_blocked_when_signed_out(anonymous, path) -> None:
    response = anonymous.post(path, data={}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_signed_out_post_changes_nothing(anonymous, store, part) -> None:
    """A blocked POST must not reach the store — asserted on the ledger."""
    anonymous.post(f"/parts/{part.id}/movements", data={"action": "restock", "amount": "99"})
    anonymous.post("/parts", data={"description": "Intruder", "category": "other"})

    assert store.get_part(part.id).quantity_on_hand == 5
    assert len(store.list_parts()) == 1


def test_redirect_body_does_not_render_the_page(anonymous, part) -> None:
    """The rejection must not leak the thing it was protecting."""
    assert "IRF7910" not in anonymous.get("/", follow_redirects=False).text


# --- what stays open --------------------------------------------------------


def test_healthz_answers_without_a_session(anonymous) -> None:
    """The Kubernetes probe. If this needs a session the pod never goes ready,
    and the deployment fails in a way that looks unrelated to auth."""
    response = anonymous.get("/healthz")

    assert response.status_code == 200
    assert response.json()["app"] == "bench-parts"


def test_login_page_is_reachable_signed_out(anonymous) -> None:
    assert anonymous.get("/login").status_code == 200


# --- signed in --------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/order", "/parts/new"])
def test_signed_in_access_is_allowed(client, path) -> None:
    assert client.get(path).status_code == 200


def test_signed_in_can_do_the_whole_flow(client, store) -> None:
    client.post(
        "/parts",
        data={
            "description": "Dual MOSFET",
            "category": "mosfet",
            "mpn": "IRF7910",
            "quantity_on_hand": "10",
            "min_quantity": "4",
        },
    )
    part = store.find_by_mpn("IRF7910")[0]
    client.post(
        f"/parts/{part.id}/movements",
        data={"action": "consume", "amount": "7", "ticket_ref": "26-0001"},
    )

    assert store.get_part(part.id).quantity_on_hand == 3
    assert "IRF7910" in client.get("/order").text


# --- the guard that has to outlive this phase -------------------------------


def test_every_route_is_protected_or_explicitly_exempt() -> None:
    """Enumerate the app's routing table and assert nothing is unaccounted for.

    This is what makes "deny by default" a property rather than a claim about
    today's code. When Phase 4 adds API routes, they're protected without
    anyone remembering — and if one is deliberately exempted, that shows up
    here as a change to `EXEMPT_PATHS` rather than passing silently.
    """
    from bench_parts.app import EXEMPT_PREFIXES

    documented = set(EXEMPT_PATHS)
    app_paths = {
        route.path
        for route in app.routes
        if getattr(route, "path", "").startswith("/")
        and route.path not in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc")
        # The static mount is exempt by prefix and serves files, not data.
        and not (route.path + "/").startswith(EXEMPT_PREFIXES)
    }

    unexpected_exempt = documented - app_paths
    assert not unexpected_exempt, f"EXEMPT_PATHS lists routes that don't exist: {unexpected_exempt}"

    # Everything else must be gated. Asserted by behaviour, not by inspection.
    # Browser routes redirect an un-sessioned request to /login (303); the
    # machine-facing /api routes answer 401 (a service wants a status code, not
    # an HTML login page). Both mean "not reachable without auth" — what must
    # never appear here is a 200.
    with TestClient(app) as probe:
        for path in sorted(app_paths - documented):
            concrete = path.replace("{part_id}", "1")
            response = probe.get(concrete, follow_redirects=False)
            allowed = (401, 405) if path.startswith("/api/") else (303, 405)
            assert response.status_code in allowed, (
                f"{path} answered {response.status_code} without auth — "
                "new routes must be protected by default"
            )


def test_exempt_set_is_minimal() -> None:
    """Each exemption is a hole; there should be very few and each obvious.

    /manifest.webmanifest joined in Phase 6: the browser fetches it signed-out
    to install the app, and it contains nothing but icon paths and a colour.
    Static files are exempt by prefix (EXEMPT_PREFIXES) for the same reason.
    """
    assert frozenset({"/healthz", "/login", "/manifest.webmanifest"}) == EXEMPT_PATHS
