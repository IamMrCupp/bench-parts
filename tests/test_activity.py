"""The activity page — the ledger, across all parts, newest first."""

import pytest


@pytest.fixture
def seeded(store):
    a = store.create_part(
        description="Dual MOSFET",
        category="mosfet",
        mpn="IRF7910",
        quantity_on_hand=10,
        unit_cost=187,
    )
    b = store.create_part(
        description="Buck IC", category="ic", mpn="TPS51980", quantity_on_hand=5, unit_cost=349
    )
    store.consume(a.id, 3, ticket_ref="26-0001", now="2026-08-19T10:00:00+00:00")
    store.consume(b.id, 1, ticket_ref="26-0001", now="2026-08-19T10:01:00+00:00")
    store.restock(a.id, 5, now="2026-08-19T10:02:00+00:00")
    store.consume(a.id, 1, ticket_ref="26-0002", now="2026-08-19T10:03:00+00:00")
    return a, b


# --- store ------------------------------------------------------------------


def test_recent_movements_newest_first_with_part_info(store, seeded) -> None:
    a, b = seeded
    rows = store.list_recent_movements(limit=10)

    assert [r.movement.delta for r in rows] == [-1, 5, -1, -3, 5, 10]
    assert rows[0].part.id == a.id
    assert rows[0].part.mpn == "IRF7910"


def test_recent_movements_respects_limit(store, seeded) -> None:
    assert len(store.list_recent_movements(limit=2)) == 2


def test_recent_movements_filtered_by_ticket(store, seeded) -> None:
    rows = store.list_recent_movements(limit=10, ticket_ref="26-0001")

    assert [r.movement.delta for r in rows] == [-1, -3]
    assert {r.part.mpn for r in rows} == {"IRF7910", "TPS51980"}


def test_recent_movements_includes_retired_parts_history(store, seeded) -> None:
    """Retiring hides a part from the catalogue, not from what happened."""
    a, _ = seeded
    store.retire_part(a.id)

    assert any(r.part.id == a.id for r in store.list_recent_movements(limit=10))


def test_ticket_spend(store, seeded) -> None:
    assert store.ticket_spend_cents("26-0001") == 3 * 187 + 1 * 349
    assert store.ticket_spend_cents("99-9999") == 0


# --- page -------------------------------------------------------------------


def test_activity_page_renders_newest_first(client, seeded) -> None:
    body = client.get("/activity").text

    assert body.index("26-0002") < body.index("26-0001")
    assert "IRF7910" in body and "TPS51980" in body
    assert "restock" in body and "consume" in body


def test_activity_links_to_the_part(client, seeded) -> None:
    a, _ = seeded
    assert f'href="/parts/{a.id}"' in client.get("/activity").text


def test_activity_filter_by_ticket_shows_spend(client, seeded) -> None:
    body = client.get("/activity", params={"ticket_ref": "26-0001"}).text

    assert "26-0002" not in body
    assert "$9.10" in body  # 3*1.87 + 3.49


def test_activity_ticket_links_filter(client, seeded) -> None:
    body = client.get("/activity").text

    assert 'href="/activity?ticket_ref=26-0001"' in body


def test_activity_empty_state(client) -> None:
    assert "No activity yet" in client.get("/activity").text


def test_activity_in_nav(client) -> None:
    assert 'href="/activity"' in client.get("/").text


def test_activity_is_gated(anonymous) -> None:
    assert anonymous.get("/activity", follow_redirects=False).status_code == 303
