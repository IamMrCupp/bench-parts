"""The part detail page — `GET /parts/{id}` and the edit form `POST /parts/{id}`.

Real form strings throughout. The one thing this page must never grow is a
raw quantity input: stock moves only through movements (B.1).
"""

import pytest


@pytest.fixture
def part(store):
    return store.create_part(
        description="Dual N-channel MOSFET",
        category="mosfet",
        mpn="IRF7910",
        manufacturer="Infineon",
        quantity_on_hand=10,
        min_quantity=4,
        unit_cost=187,
        supplier="Digi-Key",
        supplier_url="https://example.invalid/irf7910",
        location="A3",
        notes="watch the tab",
    )


def _edit_payload(part, **overrides):
    """What the pre-filled edit form posts back, as strings."""
    data = {
        "mpn": part.mpn or "",
        "manufacturer": part.manufacturer or "",
        "description": part.description,
        "category": part.category.value,
        "min_quantity": str(part.min_quantity),
        "unit_cost": "1.87",
        "supplier": part.supplier or "",
        "supplier_url": part.supplier_url or "",
        "location": part.location or "",
        "notes": part.notes or "",
    }
    data.update(overrides)
    return data


def _post(client, part, **overrides):
    return client.post(
        f"/parts/{part.id}", data=_edit_payload(part, **overrides), follow_redirects=False
    )


# --- the page ---------------------------------------------------------------


def test_detail_renders_everything_about_the_part(client, part) -> None:
    body = client.get(f"/parts/{part.id}").text

    for expected in (
        "IRF7910",
        "Infineon",
        "Dual N-channel MOSFET",
        "MOSFET",
        "A3",
        "Digi-Key",
        "watch the tab",
    ):
        assert expected in body


def test_detail_shows_cost_in_dollars(client, part) -> None:
    body = client.get(f"/parts/{part.id}").text

    assert "$1.87" in body


def test_detail_shows_count_and_status(client, part) -> None:
    body = client.get(f"/parts/{part.id}").text

    assert "status-ok" in body
    assert ">10<" in body


def test_detail_shows_movement_history(client, part, store) -> None:
    store.consume(part.id, 3, ticket_ref="26-0001")

    body = client.get(f"/parts/{part.id}").text

    assert "initial" in body
    assert "consume" in body
    assert "26-0001" in body


def test_supplier_link_is_a_real_link(client, part) -> None:
    body = client.get(f"/parts/{part.id}").text

    assert 'href="https://example.invalid/irf7910"' in body


def test_unknown_part_is_404(client) -> None:
    assert client.get("/parts/9999").status_code == 404


# --- the edit form ----------------------------------------------------------


def test_edit_form_is_prefilled(client, part) -> None:
    body = client.get(f"/parts/{part.id}").text

    assert 'name="description"' in body
    assert 'value="IRF7910"' in body
    assert 'value="A3"' in body
    assert 'value="mosfet" selected' in body


def test_edit_form_prefills_cost_in_dollars(client, part) -> None:
    body = client.get(f"/parts/{part.id}").text

    assert 'name="unit_cost"' in body
    assert 'value="1.87"' in body


def test_edit_form_has_no_quantity_field(client, part) -> None:
    """The one field this form must never grow. Stock moves through B.1."""
    body = client.get(f"/parts/{part.id}").text

    assert 'name="quantity_on_hand"' not in body


def test_edit_form_lets_you_change_min_quantity(client, part) -> None:
    """A threshold is policy, not a count — safe to edit here."""
    body = client.get(f"/parts/{part.id}").text

    assert 'name="min_quantity"' in body


# --- editing ----------------------------------------------------------------


def test_edit_changes_a_field_and_redirects_back(client, part, store) -> None:
    response = _post(client, part, location="C9")

    assert response.status_code == 303
    assert response.headers["location"] == f"/parts/{part.id}"
    assert store.get_part(part.id).location == "C9"


def test_edit_updates_every_catalogue_field(client, part, store) -> None:
    _post(
        client,
        part,
        mpn="IRF7910PBF",
        manufacturer="Vishay",
        description="Dual N-ch MOSFET (lead-free)",
        category="transistor_bjt",
        min_quantity="8",
        unit_cost="2.10",
        supplier="Mouser",
        supplier_url="https://example.invalid/new",
        location="C9",
        notes="reel 2",
    )

    updated = store.get_part(part.id)

    assert updated.mpn == "IRF7910PBF"
    assert updated.manufacturer == "Vishay"
    assert updated.description == "Dual N-ch MOSFET (lead-free)"
    assert updated.category.value == "transistor_bjt"
    assert updated.min_quantity == 8
    assert updated.unit_cost == 210
    assert updated.supplier == "Mouser"
    assert updated.supplier_url == "https://example.invalid/new"
    assert updated.location == "C9"
    assert updated.notes == "reel 2"


def test_edit_does_not_touch_the_quantity(client, part, store) -> None:
    """Even a payload that smuggles a quantity in is ignored, not applied."""
    client.post(
        f"/parts/{part.id}",
        data={**_edit_payload(part), "quantity_on_hand": "999"},
        follow_redirects=False,
    )

    assert store.get_part(part.id).quantity_on_hand == 10
    assert len(store.list_movements(part.id)) == 1


def test_edit_does_not_write_a_movement(client, part, store) -> None:
    _post(client, part, location="C9")

    assert len(store.list_movements(part.id)) == 1  # the initial only


def test_edit_bumps_updated_at_not_created_at(client, part, store) -> None:
    _post(client, part, location="C9")

    updated = store.get_part(part.id)

    assert updated.created_at == part.created_at
    assert updated.updated_at >= part.updated_at


def test_edit_clears_a_field_when_blanked(client, part, store) -> None:
    _post(client, part, notes="", location="")

    updated = store.get_part(part.id)

    assert updated.notes is None
    assert updated.location is None


def test_edit_clears_cost_when_blanked(client, part, store) -> None:
    _post(client, part, unit_cost="")

    assert store.get_part(part.id).unit_cost is None


def test_edit_cost_change_does_not_rewrite_history(client, part, store) -> None:
    """The reason cost is snapshotted onto movements."""
    store.consume(part.id, 2, ticket_ref="26-0001")

    _post(client, part, unit_cost="9.99")

    assert store.get_part(part.id).unit_cost == 999
    assert store.list_movements(part.id)[-1].unit_cost_at_time == 187


# --- validation -------------------------------------------------------------


def test_blank_description_re_renders_with_error(client, part, store) -> None:
    response = _post(client, part, description="")

    assert response.status_code == 400
    assert "description" in response.text.lower()
    assert store.get_part(part.id).description == "Dual N-channel MOSFET"


def test_error_preserves_the_attempted_values(client, part) -> None:
    body = _post(client, part, description="", location="C9", supplier="Mouser").text

    assert 'value="C9"' in body
    assert 'value="Mouser"' in body


def test_error_still_shows_the_part_and_its_history(client, part, store) -> None:
    """A rejected edit must not strip the page down to a bare form."""
    store.consume(part.id, 1, ticket_ref="26-0001")

    body = _post(client, part, description="").text

    assert "26-0001" in body
    assert "status-" in body


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("category", "mosfets"),
        ("min_quantity", "-1"),
        ("min_quantity", "four"),
        ("unit_cost", "cheap"),
        ("unit_cost", "-1"),
    ],
)
def test_bad_values_are_400_not_500(client, part, store, field, value) -> None:
    response = _post(client, part, **{field: value})

    assert response.status_code == 400
    assert store.get_part(part.id).min_quantity == 4


def test_edit_of_unknown_part_is_404(client, part) -> None:
    response = client.post("/parts/9999", data=_edit_payload(part), follow_redirects=False)

    assert response.status_code == 404
