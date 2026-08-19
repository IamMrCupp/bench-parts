"""The order page grouped by supplier, with bulk-add text and restock-from-row.

Stage A of "build me a cart": no supplier credentials, but the page now
produces exactly what Digi-Key's and Mouser's paste-a-BOM tools accept, and a
delivery can be logged without opening each part.
"""

import pytest

from bench_parts.schema import SCHEMA_VERSION


@pytest.fixture
def seeded(store):
    dk = store.create_part(
        description="MOSFET",
        category="mosfet",
        mpn="IRF7910",
        supplier="Digi-Key",
        supplier_pn="IRF7910PBFCT-ND",
        quantity_on_hand=1,
        min_quantity=4,
        reorder_qty=10,
    )
    mo = store.create_part(
        description="Buck IC",
        category="ic",
        mpn="TPS51980",
        supplier="Mouser",
        supplier_pn="595-TPS51980RTVR",
        quantity_on_hand=0,
        min_quantity=2,
    )
    mo2 = store.create_part(
        description="Cap",
        category="capacitor",
        mpn="GRM188",
        supplier="mouser",
        quantity_on_hand=2,
        min_quantity=5,
    )  # no supplier_pn -> falls back to MPN; lower-case supplier
    am = store.create_part(
        description="LED strip",
        category="led_opto",
        mpn="WS2815",
        supplier="Amazon",
        supplier_url="https://www.amazon.com/dp/B0XYZ",
        quantity_on_hand=0,
        min_quantity=1,
    )
    none = store.create_part(
        description="Mystery", category="other", quantity_on_hand=0, min_quantity=3
    )
    ok = store.create_part(
        description="Fine", category="other", mpn="FINE", quantity_on_hand=9, min_quantity=1
    )
    return dk, mo, mo2, am, none, ok


def test_schema_is_at_least_v5() -> None:
    assert SCHEMA_VERSION >= 5


# --- store: new fields + order quantity ------------------------------------


def test_supplier_pn_and_reorder_qty_round_trip(store, seeded) -> None:
    dk, *_ = seeded
    assert dk.supplier_pn == "IRF7910PBFCT-ND"
    assert dk.reorder_qty == 10
    assert store.update_part(dk.id, reorder_qty=25, supplier_pn="X").reorder_qty == 25


def test_order_qty_prefers_reorder_qty(store, seeded) -> None:
    dk, mo, mo2, *_ = seeded
    assert dk.order_qty == 10  # reorder_qty set
    assert mo.order_qty == 2  # shortfall 2
    assert mo2.order_qty == 3  # shortfall 3


def test_order_qty_is_at_least_one_for_min_zero_parts(store) -> None:
    p = store.create_part(description="x", category="other", quantity_on_hand=0, min_quantity=0)
    assert p.order_qty == 1


def test_reorder_qty_must_be_positive(store) -> None:
    with pytest.raises(ValueError):
        store.create_part(description="x", category="other", reorder_qty=0)


def test_below_min_grouped_by_supplier(store, seeded) -> None:
    groups = store.below_min_by_supplier()

    assert [g.name for g in groups] == ["Amazon", "Digi-Key", "Mouser", "(no supplier)"]
    mouser = next(g for g in groups if g.name == "Mouser")
    assert [p.mpn for p in mouser.parts] == ["TPS51980", "GRM188"]  # out before low
    assert groups[-1].parts[0].description == "Mystery"


def test_supplier_grouping_is_case_insensitive(store, seeded) -> None:
    """'mouser' and 'Mouser' are one basket."""
    names = [g.name for g in store.below_min_by_supplier()]
    assert names.count("Mouser") == 1 and "mouser" not in names


def test_bulk_add_text_uses_supplier_pn_then_mpn(store, seeded) -> None:
    mouser = next(g for g in store.below_min_by_supplier() if g.name == "Mouser")

    assert mouser.bulk_add_text() == "595-TPS51980RTVR,2\nGRM188,3"


def test_bulk_add_text_skips_parts_with_no_number(store, seeded) -> None:
    nothing = next(g for g in store.below_min_by_supplier() if g.name == "(no supplier)")
    assert nothing.bulk_add_text() == ""


def test_order_csv(store, seeded) -> None:
    csv = store.order_csv()

    assert (
        csv.splitlines()[0]
        == "supplier,supplier_pn,mpn,description,on_hand,min,order_qty,supplier_url"
    )
    assert "Digi-Key,IRF7910PBFCT-ND,IRF7910,MOSFET,1,4,10," in csv
    assert "FINE" not in csv


# --- page -------------------------------------------------------------------


def test_order_page_is_grouped_by_supplier(client, seeded) -> None:
    body = client.get("/order").text

    assert (
        body.index("Amazon")
        < body.index("Digi-Key")
        < body.index("Mouser")
        < body.index("no supplier")
    )


def test_order_page_shows_order_qty(client, seeded) -> None:
    body = client.get("/order").text
    assert ">10<" in body  # IRF7910's reorder_qty


def test_order_page_has_bulk_add_block_per_api_supplier(client, seeded) -> None:
    body = client.get("/order").text

    assert "595-TPS51980RTVR,2" in body
    assert "IRF7910PBFCT-ND,10" in body
    assert "https://www.mouser.com/Tools/BOM" in body or "mouser.com" in body
    assert "digikey.com" in body


def test_order_page_amazon_rows_link_out(client, seeded) -> None:
    assert 'href="https://www.amazon.com/dp/B0XYZ"' in client.get("/order").text


def test_restock_from_order_row(client, store, seeded) -> None:
    dk, *_ = seeded
    body = client.get("/order").text
    assert f'action="/parts/{dk.id}/movements"' in body
    assert 'name="next" value="/order"' in body

    r = client.post(
        f"/parts/{dk.id}/movements",
        data={"action": "restock", "amount": "10", "next": "/order"},
        follow_redirects=False,
    )

    assert r.status_code == 303
    assert r.headers["location"] == "/order"
    assert store.get_part(dk.id).quantity_on_hand == 11


def test_restock_next_is_safe(client, store, seeded) -> None:
    dk, *_ = seeded
    r = client.post(
        f"/parts/{dk.id}/movements",
        data={"action": "restock", "amount": "1", "next": "https://evil.example"},
        follow_redirects=False,
    )
    assert r.headers["location"] == f"/parts/{dk.id}"


def test_order_csv_download(client, seeded) -> None:
    r = client.get("/order.csv")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "IRF7910" in r.text


def test_forms_have_the_new_fields(client, seeded) -> None:
    body = client.get("/parts/new").text
    assert 'name="supplier_pn"' in body and 'name="reorder_qty"' in body
    dk, *_ = seeded
    assert 'value="IRF7910PBFCT-ND"' in client.get(f"/parts/{dk.id}").text


def test_api_carries_the_new_fields(client, seeded) -> None:
    dk, *_ = seeded
    body = client.get(f"/api/parts/{dk.id}").json()
    assert body["supplier_pn"] == "IRF7910PBFCT-ND"
    assert body["reorder_qty"] == 10
    assert body["order_qty"] == 10
