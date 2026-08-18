"""Adding a part — `GET /parts/new` and `POST /parts`.

Every POST here sends **real form data as strings**, blanks as `""`. That is
what a tablet sends, and it's the path intake's Phase 1 bug hid behind.
"""

import pytest

VALID = {
    "mpn": "IRF7910",
    "manufacturer": "Infineon",
    "description": "Dual N-channel MOSFET",
    "category": "mosfet",
    "quantity_on_hand": "10",
    "min_quantity": "4",
    "unit_cost": "1.87",
    "supplier": "Digi-Key",
    "supplier_url": "https://example.invalid/irf7910",
    "location": "A3",
    "notes": "",
}


def _post(client, **overrides):
    data = {**VALID, **overrides}
    return client.post("/parts", data=data, follow_redirects=False)


# --- the form ---------------------------------------------------------------


def test_form_renders(client) -> None:
    response = client.get("/parts/new")

    assert response.status_code == 200
    assert 'name="description"' in response.text
    assert 'name="category"' in response.text


def test_form_offers_every_category_by_label(client) -> None:
    body = client.get("/parts/new").text

    assert 'value="mosfet"' in body
    assert "Transistor (BJT)" in body


def test_form_uses_numeric_keyboards_for_counts(client) -> None:
    """A tablet should bring up the number pad for a count, not QWERTY."""
    body = client.get("/parts/new").text

    assert 'inputmode="numeric"' in body
    assert 'inputmode="decimal"' in body


def test_form_has_no_raw_quantity_field_after_creation(client) -> None:
    """Opening stock is allowed on *create* (it becomes an `initial` movement).
    The field is named for what it is, so nobody mistakes it for a live count."""
    body = client.get("/parts/new").text

    assert 'name="quantity_on_hand"' in body


# --- creating ---------------------------------------------------------------


def test_post_creates_and_redirects_to_the_part(client, store) -> None:
    response = _post(client)

    assert response.status_code == 303
    parts = store.list_parts()
    assert len(parts) == 1
    assert response.headers["location"] == f"/parts/{parts[0].id}"


def test_post_stores_every_field(client, store) -> None:
    _post(client)

    part = store.list_parts()[0]

    assert part.mpn == "IRF7910"
    assert part.manufacturer == "Infineon"
    assert part.description == "Dual N-channel MOSFET"
    assert part.category.value == "mosfet"
    assert part.min_quantity == 4
    assert part.supplier == "Digi-Key"
    assert part.supplier_url == "https://example.invalid/irf7910"
    assert part.location == "A3"


def test_opening_stock_becomes_an_initial_movement(client, store) -> None:
    """The invariant from Phase 1, holding at the HTTP edge."""
    _post(client, quantity_on_hand="10")

    part = store.list_parts()[0]
    movements = store.list_movements(part.id)

    assert part.quantity_on_hand == 10
    assert len(movements) == 1
    assert movements[0].reason.value == "initial"
    assert movements[0].delta == 10


def test_cost_is_entered_in_dollars_and_stored_in_cents(client, store) -> None:
    _post(client, unit_cost="1.87")

    assert store.list_parts()[0].unit_cost == 187


@pytest.mark.parametrize("entered", ["0.5", ".50", "0.50", " 0.5 "])
def test_cost_accepts_reasonable_human_input(client, store, entered) -> None:
    _post(client, unit_cost=entered)

    assert store.list_parts()[0].unit_cost == 50


def test_cost_rounds_sub_cent_input(client, store) -> None:
    """A price list might say $0.0187 each. Store the nearest cent, don't reject."""
    _post(client, unit_cost="0.0187")

    assert store.list_parts()[0].unit_cost == 2


def test_blank_cost_is_none(client, store) -> None:
    _post(client, unit_cost="")

    assert store.list_parts()[0].unit_cost is None


def test_blank_optional_fields_land_as_null(client, store) -> None:
    """A form posts '' — the row must not carry empty strings."""
    _post(client, mpn="", manufacturer="", supplier="", supplier_url="", location="", notes="")

    part = store.list_parts()[0]

    assert part.mpn is None
    assert part.manufacturer is None
    assert part.supplier is None
    assert part.supplier_url is None
    assert part.location is None
    assert part.notes is None


def test_padded_input_is_stripped(client, store) -> None:
    _post(client, mpn="  IRF7910 ", location=" A3 ")

    part = store.list_parts()[0]

    assert part.mpn == "IRF7910"
    assert part.location == "A3"


def test_blank_counts_default_to_zero(client, store) -> None:
    """Leaving the count blank means 'none yet', not an error."""
    _post(client, quantity_on_hand="", min_quantity="")

    part = store.list_parts()[0]

    assert part.quantity_on_hand == 0
    assert part.min_quantity == 0
    assert store.list_movements(part.id) == []


# --- validation: re-render, don't punish ------------------------------------


def test_blank_description_re_renders_with_error(client, store) -> None:
    response = _post(client, description="")

    assert response.status_code == 400
    assert "description" in response.text.lower()
    assert store.list_parts() == []


def test_error_preserves_everything_else_typed(client) -> None:
    """Retyping on a tablet is how a tool stops getting used."""
    body = _post(client, description="", mpn="IRF7910", location="A3", supplier="Digi-Key").text

    assert 'value="IRF7910"' in body
    assert 'value="A3"' in body
    assert 'value="Digi-Key"' in body


def test_error_keeps_the_chosen_category_selected(client) -> None:
    body = _post(client, description="", category="capacitor").text

    assert 'value="capacitor" selected' in body


def test_unknown_category_is_rejected(client, store) -> None:
    response = _post(client, category="mosfets")

    assert response.status_code == 400
    assert store.list_parts() == []


def test_negative_count_is_rejected(client, store) -> None:
    response = _post(client, quantity_on_hand="-1")

    assert response.status_code == 400
    assert store.list_parts() == []


def test_non_numeric_count_is_a_readable_error_not_a_500(client, store) -> None:
    response = _post(client, min_quantity="four")

    assert response.status_code == 400
    assert store.list_parts() == []


def test_non_numeric_cost_is_a_readable_error_not_a_500(client, store) -> None:
    response = _post(client, unit_cost="cheap")

    assert response.status_code == 400
    assert store.list_parts() == []


def test_negative_cost_is_rejected(client, store) -> None:
    response = _post(client, unit_cost="-1.00")

    assert response.status_code == 400
    assert store.list_parts() == []


def test_a_rejected_create_writes_nothing(client, store) -> None:
    """Validate before mutate — no part, no movement, nothing to clean up."""
    _post(client, description="", quantity_on_hand="10")

    assert store.list_parts() == []
    with store._conn:
        n = store._conn.execute("SELECT COUNT(*) FROM movements").fetchone()[0]
    assert n == 0


# --- unicode / long input ---------------------------------------------------


def test_unicode_and_long_input_round_trip(client, store) -> None:
    long_notes = "Ω" * 2000
    _post(client, description="Résistance 4.7kΩ ¼W", notes=long_notes)

    part = store.list_parts()[0]

    assert part.description == "Résistance 4.7kΩ ¼W"
    assert part.notes == long_notes
    assert client.get("/").status_code == 200
