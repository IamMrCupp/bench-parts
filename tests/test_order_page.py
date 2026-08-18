"""The "to order" page — `GET /order`. The reason the phase exists.

The page must agree with the catalogue colours: a part that shows amber there
must appear here. Both read `bench_parts.stock`, so the cross-check test at
the bottom is a regression guard, not a hope.
"""

import re

from bench_parts.stock import is_below_min


def _seed(store):
    store.create_part(
        description="Plenty", category="mosfet", mpn="OK-1", quantity_on_hand=20, min_quantity=4
    )
    store.create_part(
        description="Barely low",
        category="ic",
        mpn="LOW-BARELY",
        quantity_on_hand=5,
        min_quantity=5,
        supplier="Mouser",
        supplier_url="https://example.invalid/low-barely",
    )
    store.create_part(
        description="Deep low",
        category="capacitor",
        mpn="LOW-DEEP",
        quantity_on_hand=1,
        min_quantity=50,
    )
    store.create_part(
        description="Gone",
        category="resistor",
        mpn="OUT-1",
        quantity_on_hand=0,
        min_quantity=10,
        supplier="Digi-Key",
    )
    store.create_part(
        description="No threshold, stocked",
        category="other",
        mpn="NOFLAG",
        quantity_on_hand=2,
        min_quantity=0,
    )
    store.create_part(
        description="No threshold, gone",
        category="other",
        mpn="GONE-NOFLAG",
        quantity_on_hand=0,
        min_quantity=0,
    )


def test_order_page_renders(client) -> None:
    response = client.get("/order")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_empty_state_says_nothing_to_order(client) -> None:
    body = client.get("/order").text

    assert "Nothing to order" in body


def test_lists_only_parts_at_or_below_min(client, store) -> None:
    _seed(store)

    body = client.get("/order").text

    assert "LOW-BARELY" in body
    assert "LOW-DEEP" in body
    assert "OUT-1" in body
    assert "GONE-NOFLAG" in body
    assert "OK-1" not in body
    # NOFLAG (stocked, min 0) must be absent; GONE-NOFLAG (zero, min 0) present.
    # Count the substring: it appears exactly once, inside "GONE-NOFLAG".
    assert body.count("NOFLAG") == 1


def test_out_comes_before_low(client, store) -> None:
    """Most urgent first — you read this while placing the order."""
    _seed(store)

    body = client.get("/order").text

    assert body.index("OUT-1") < body.index("LOW-DEEP")
    assert body.index("GONE-NOFLAG") < body.index("LOW-DEEP")


def test_deeper_shortfall_comes_first_within_low(client, store) -> None:
    _seed(store)

    body = client.get("/order").text

    assert body.index("LOW-DEEP") < body.index("LOW-BARELY")


def test_rows_carry_their_status_class(client, store) -> None:
    _seed(store)

    body = client.get("/order").text

    assert "status-out" in body
    assert "status-low" in body
    assert "status-ok" not in body


def test_shows_on_hand_min_and_shortfall(client, store) -> None:
    """The three numbers that decide how many to buy."""
    _seed(store)

    body = client.get("/order").text

    # LOW-DEEP: 1 on hand, min 50, short by 49
    assert "49" in body


def test_supplier_url_is_a_direct_link(client, store) -> None:
    """The whole point of storing it: tap, land on the reorder page."""
    _seed(store)

    body = client.get("/order").text

    assert 'href="https://example.invalid/low-barely"' in body


def test_supplier_without_url_still_shows_the_name(client, store) -> None:
    _seed(store)

    body = client.get("/order").text

    assert "Digi-Key" in body


def test_each_row_links_to_the_part(client, store) -> None:
    _seed(store)
    part = store.find_by_mpn("OUT-1")[0]

    body = client.get("/order").text

    assert f'href="/parts/{part.id}"' in body


def test_reflects_a_consumption_immediately(client, store) -> None:
    """A live view of stock, not a snapshot."""
    part = store.create_part(
        description="x", category="mosfet", mpn="EDGE", quantity_on_hand=6, min_quantity=5
    )
    assert "EDGE" not in client.get("/order").text

    store.consume(part.id, 1, ticket_ref="26-0001")

    assert "EDGE" in client.get("/order").text


def test_page_agrees_with_the_catalogue_colours(client, store) -> None:
    """The regression guard: every part the catalogue marks low/out is on this
    page, and nothing it marks ok is."""
    for quantity in range(0, 6):
        for minimum in range(0, 6):
            store.create_part(
                description="grid",
                category="other",
                mpn=f"Q{quantity}M{minimum}",
                quantity_on_hand=quantity,
                min_quantity=minimum,
            )

    order_body = client.get("/order").text
    catalogue_body = client.get("/").text
    on_order_page = set(re.findall(r">(Q\dM\d)<", order_body))
    marked_low_or_out = set()
    for row in re.findall(r'<tr class="status-(\w+)">(.*?)</tr>', catalogue_body, flags=re.S):
        status, html = row
        mpn = re.search(r">(Q\dM\d)<", html)
        if mpn and status in ("low", "out"):
            marked_low_or_out.add(mpn.group(1))

    expected = {f"Q{q}M{m}" for q in range(6) for m in range(6) if is_below_min(q, m)}
    assert on_order_page == expected
    assert marked_low_or_out == expected
