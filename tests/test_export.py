"""CSV export — your data, out."""

import csv
import io


def _rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def test_parts_export_has_every_part_and_field(client, store) -> None:
    a = store.create_part(
        description="Dual MOSFET",
        category="mosfet",
        mpn="IRF7910",
        quantity_on_hand=10,
        min_quantity=4,
        unit_cost=187,
        supplier="Digi-Key",
        supplier_pn="X-ND",
        reorder_qty=10,
        location="A3",
        project="bench",
        notes="n",
    )
    old = store.create_part(description="Old", category="other", mpn="OLD")
    store.retire_part(old.id)

    r = client.get("/export.csv")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = _rows(r.text)
    assert {row["mpn"] for row in rows} == {"IRF7910", "OLD"}
    row = next(x for x in rows if x["mpn"] == "IRF7910")
    for key in (
        "id",
        "description",
        "category",
        "quantity_on_hand",
        "min_quantity",
        "unit_cost_cents",
        "supplier",
        "supplier_pn",
        "reorder_qty",
        "location",
        "project",
        "notes",
        "status",
        "retired_at",
        "created_at",
        "updated_at",
    ):
        assert key in row, key
    assert row["unit_cost_cents"] == "187" and row["status"] == "ok"
    assert next(x for x in rows if x["mpn"] == "OLD")["retired_at"]
    assert a.id == int(row["id"])


def test_movements_export(client, store) -> None:
    p = store.create_part(
        description="x", category="other", mpn="X", quantity_on_hand=5, unit_cost=100
    )
    store.consume(p.id, 2, ticket_ref="26-0001")

    r = client.get("/export/movements.csv")

    rows = _rows(r.text)
    assert [x["reason"] for x in rows] == ["initial", "consume"]
    assert rows[1]["ticket_ref"] == "26-0001" and rows[1]["unit_cost_cents_at_time"] == "100"
    assert rows[1]["mpn"] == "X"


def test_exports_are_gated(anonymous) -> None:
    assert anonymous.get("/export.csv", follow_redirects=False).status_code == 303
    assert anonymous.get("/export/movements.csv", follow_redirects=False).status_code == 303


def test_export_link_on_the_catalogue(client) -> None:
    assert 'href="/export.csv"' in client.get("/").text


def test_export_handles_commas_quotes_and_newlines(client, store) -> None:
    store.create_part(description='Cap, 100nF "X7R"\nline two', category="capacitor")

    rows = _rows(client.get("/export.csv").text)

    assert rows[0]["description"] == 'Cap, 100nF "X7R"\nline two'
