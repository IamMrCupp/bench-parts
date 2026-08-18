"""Restock / consume / correct from the detail page — `POST /parts/{id}/movements`.

The only way the UI moves a count. Every request here is real form data, and
every assertion about stock reads the ledger, not just the cached number.
"""

import pytest


@pytest.fixture
def part(store):
    return store.create_part(
        description="Dual N-channel MOSFET",
        category="mosfet",
        mpn="IRF7910",
        quantity_on_hand=10,
        min_quantity=4,
        unit_cost=187,
    )


def _move(client, part, action, amount, **extra):
    data = {"action": action, "amount": str(amount), **extra}
    return client.post(f"/parts/{part.id}/movements", data=data, follow_redirects=False)


def _log_sum(store, part_id):
    return sum(m.delta for m in store.list_movements(part_id))


# --- the controls exist -----------------------------------------------------


def test_detail_page_has_the_three_controls(client, part) -> None:
    body = client.get(f"/parts/{part.id}").text

    assert 'value="restock"' in body
    assert 'value="consume"' in body
    assert 'value="correct"' in body
    assert 'name="ticket_ref"' in body


def test_amount_fields_use_the_numeric_keyboard(client, part) -> None:
    body = client.get(f"/parts/{part.id}").text

    assert body.count('inputmode="numeric"') >= 3


# --- restock ----------------------------------------------------------------


def test_restock_adds_and_redirects_back(client, part, store) -> None:
    response = _move(client, part, "restock", 5)

    assert response.status_code == 303
    assert response.headers["location"] == f"/parts/{part.id}"
    assert store.get_part(part.id).quantity_on_hand == 15


def test_restock_writes_a_ledger_row(client, part, store) -> None:
    _move(client, part, "restock", 5)

    last = store.list_movements(part.id)[-1]

    assert last.reason.value == "restock"
    assert last.delta == 5


# --- consume ----------------------------------------------------------------


def test_consume_subtracts(client, part, store) -> None:
    _move(client, part, "consume", 3)

    assert store.get_part(part.id).quantity_on_hand == 7
    assert store.list_movements(part.id)[-1].delta == -3


def test_consume_records_the_ticket_ref(client, part, store) -> None:
    _move(client, part, "consume", 2, ticket_ref="26-0001")

    assert store.list_movements(part.id)[-1].ticket_ref == "26-0001"


def test_consume_ticket_ref_is_stored_verbatim(client, part, store) -> None:
    """Never validated against intake — loose coupling is the point."""
    _move(client, part, "consume", 1, ticket_ref="not-a-real-ref")

    assert store.list_movements(part.id)[-1].ticket_ref == "not-a-real-ref"


def test_consume_blank_ticket_ref_is_none(client, part, store) -> None:
    _move(client, part, "consume", 1, ticket_ref="")

    assert store.list_movements(part.id)[-1].ticket_ref is None


def test_consume_snapshots_the_cost(client, part, store) -> None:
    _move(client, part, "consume", 1, ticket_ref="26-0001")

    assert store.list_movements(part.id)[-1].unit_cost_at_time == 187


def test_consume_exactly_the_stock_is_fine(client, part, store) -> None:
    _move(client, part, "consume", 10)

    assert store.get_part(part.id).quantity_on_hand == 0
    assert store.get_part(part.id).status.value == "out"


# --- correct ----------------------------------------------------------------


def test_correct_up(client, part, store) -> None:
    _move(client, part, "correct", 3)

    assert store.get_part(part.id).quantity_on_hand == 13
    assert store.list_movements(part.id)[-1].reason.value == "correction"


def test_correct_down(client, part, store) -> None:
    _move(client, part, "correct", -4)

    assert store.get_part(part.id).quantity_on_hand == 6
    assert store.list_movements(part.id)[-1].delta == -4


# --- rejections: readable, page intact, nothing written --------------------


def test_over_consumption_is_a_readable_error(client, part, store) -> None:
    response = _move(client, part, "consume", 11)

    assert response.status_code == 400
    assert "10" in response.text  # the message names what's actually on hand
    assert store.get_part(part.id).quantity_on_hand == 10
    assert len(store.list_movements(part.id)) == 1


def test_error_page_still_shows_the_part(client, part, store) -> None:
    """A rejected movement must not strip the page down to a bare error."""
    body = _move(client, part, "consume", 11).text

    assert "IRF7910" in body
    assert 'value="restock"' in body


@pytest.mark.parametrize("amount", ["0", "-1", "", "three"])
def test_bad_restock_amount_is_400(client, part, store, amount) -> None:
    response = _move(client, part, "restock", amount)

    assert response.status_code == 400
    assert store.get_part(part.id).quantity_on_hand == 10


@pytest.mark.parametrize("amount", ["0", "-1", "", "three"])
def test_bad_consume_amount_is_400(client, part, store, amount) -> None:
    """A negative 'consume' must not silently add stock."""
    response = _move(client, part, "consume", amount)

    assert response.status_code == 400
    assert store.get_part(part.id).quantity_on_hand == 10


@pytest.mark.parametrize("amount", ["0", "", "lots"])
def test_bad_correction_amount_is_400(client, part, store, amount) -> None:
    response = _move(client, part, "correct", amount)

    assert response.status_code == 400
    assert store.get_part(part.id).quantity_on_hand == 10


def test_correction_below_zero_is_rejected(client, part, store) -> None:
    response = _move(client, part, "correct", -11)

    assert response.status_code == 400
    assert store.get_part(part.id).quantity_on_hand == 10


def test_unknown_action_is_400(client, part, store) -> None:
    response = _move(client, part, "shrinkage", 1)

    assert response.status_code == 400
    assert len(store.list_movements(part.id)) == 1


def test_movement_on_unknown_part_is_404(client) -> None:
    response = client.post(
        "/parts/9999/movements", data={"action": "restock", "amount": "1"}, follow_redirects=False
    )

    assert response.status_code == 404


# --- the invariant, at the HTTP edge ---------------------------------------


def test_cache_matches_ledger_after_a_sequence(client, part, store) -> None:
    _move(client, part, "restock", 5)
    _move(client, part, "consume", 3, ticket_ref="26-0001")
    _move(client, part, "correct", -2)
    _move(client, part, "consume", 11)  # rejected

    assert store.get_part(part.id).quantity_on_hand == _log_sum(store, part.id) == 10
    assert len(store.list_movements(part.id)) == 4  # initial + 3, the rejection wrote nothing


def test_history_shows_the_new_movement(client, part) -> None:
    _move(client, part, "consume", 2, ticket_ref="26-0001")

    body = client.get(f"/parts/{part.id}").text

    assert "consume" in body
    assert "26-0001" in body
    assert "-2" in body
