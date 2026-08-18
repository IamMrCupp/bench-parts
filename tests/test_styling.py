"""Structural hooks the responsive layout depends on.

Styling itself is a judgment call for a human with a tablet (acceptance test
10). What *can* be pinned is that the markup carries the hooks the CSS keys on
— strip the `parts` class from a table and the phone layout silently reverts to
an overflowing table with the count off-screen, which is exactly the finding
this pass fixed.
"""


def _seed(store):
    store.create_part(
        description="x", category="mosfet", mpn="IRF7910", quantity_on_hand=1, min_quantity=5
    )


def test_catalogue_table_carries_the_responsive_hooks(client, store) -> None:
    _seed(store)

    body = client.get("/").text

    assert '<table class="parts">' in body
    for cell in ("mpn", "desc", "cat", "loc", "stock"):
        assert f'class="{cell}"' in body


def test_order_table_carries_the_responsive_hooks(client, store) -> None:
    _seed(store)

    body = client.get("/order").text

    assert '<table class="parts">' in body
    for cell in ("mpn", "desc", "stock", "short", "supplier"):
        assert cell in body


def test_narrow_layout_rules_ship_in_the_base(client) -> None:
    """The media query lives inline in base.html — no build step, no external
    stylesheet to forget to deploy."""
    body = client.get("/").text

    assert "@media (max-width: 40rem)" in body
    assert "table.parts" in body


def test_touch_target_floor_is_declared(client) -> None:
    body = client.get("/").text

    assert "--touch: 44px" in body
    assert 'name="viewport"' in body
