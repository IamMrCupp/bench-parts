"""PATCH /api/parts/{id} — explicit partial semantics, unlike the web form."""


def _mk(store, **kw):
    defaults = {
        "description": "Dual MOSFET",
        "category": "mosfet",
        "mpn": "IRF7910",
        "manufacturer": "Infineon",
        "quantity_on_hand": 10,
        "min_quantity": 4,
        "unit_cost": 187,
        "supplier": "Digi-Key",
        "location": "A3",
        "notes": "keep",
        "project": "bench",
    }
    defaults.update(kw)
    return store.create_part(**defaults)


def test_patch_changes_only_the_keys_sent(client, store) -> None:
    part = _mk(store)

    r = client.patch(f"/api/parts/{part.id}", json={"location": "B7"})

    assert r.status_code == 200
    body = r.json()
    assert body["location"] == "B7"
    # everything omitted is untouched — the web form's clear-on-omit must not leak in
    assert body["mpn"] == "IRF7910"
    assert body["unit_cost_cents"] == 187
    assert body["notes"] == "keep"
    assert store.get_part(part.id).location == "B7"


def test_patch_null_clears_a_field(client, store) -> None:
    part = _mk(store)

    r = client.patch(f"/api/parts/{part.id}", json={"notes": None, "project": None})

    assert r.status_code == 200
    assert r.json()["notes"] is None
    assert r.json()["project"] is None


def test_patch_unit_cost_cents_maps_to_store_unit_cost(client, store) -> None:
    part = _mk(store)

    r = client.patch(f"/api/parts/{part.id}", json={"unit_cost_cents": 205})

    assert r.status_code == 200
    assert r.json()["unit_cost_cents"] == 205
    assert store.get_part(part.id).unit_cost == 205


def test_patch_refuses_quantity_on_hand_with_the_movement_message(client, store) -> None:
    part = _mk(store)

    r = client.patch(f"/api/parts/{part.id}", json={"quantity_on_hand": 99})

    assert r.status_code == 400
    assert "movement" in r.json()["detail"]
    assert store.get_part(part.id).quantity_on_hand == 10


def test_patch_rejects_unknown_keys(client, store) -> None:
    part = _mk(store)

    assert client.patch(f"/api/parts/{part.id}", json={"quantify": 1}).status_code == 422


def test_patch_rejects_wrong_types(client, store) -> None:
    part = _mk(store)

    assert client.patch(f"/api/parts/{part.id}", json={"min_quantity": "4"}).status_code == 422


def test_patch_validates_category(client, store) -> None:
    part = _mk(store)

    r = client.patch(f"/api/parts/{part.id}", json={"category": "capacitator"})

    assert r.status_code == 400
    assert "category" in r.json()["detail"]


def test_patch_unknown_part_is_404(client) -> None:
    assert client.patch("/api/parts/9999", json={"notes": "x"}).status_code == 404


def test_patch_empty_body_is_a_no_op(client, store) -> None:
    part = _mk(store)

    r = client.patch(f"/api/parts/{part.id}", json={})

    assert r.status_code == 200
    assert r.json()["mpn"] == "IRF7910"


def test_patch_requires_auth(anonymous, store) -> None:
    part = _mk(store)

    assert anonymous.patch(f"/api/parts/{part.id}", json={"notes": "x"}).status_code == 401
