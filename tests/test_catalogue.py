"""The catalogue — `GET /`, the primary screen.

Tests assert on things a person at a bench would look for (MPN, count, the
colour of the row) and on the request/response contract (status codes, the
400 for a bad filter). They don't pin exact markup: Phase 2's styling pass
should be free to move things around without rewriting these.
"""

import pytest


def _seed(store):
    a = store.create_part(
        description="Dual N-channel MOSFET",
        category="mosfet",
        mpn="IRF7910",
        quantity_on_hand=10,
        min_quantity=4,
        location="A3",
    )
    b = store.create_part(
        description="Buck controller",
        category="ic",
        mpn="TPS51980",
        quantity_on_hand=2,
        min_quantity=5,
        location="B7",
    )
    c = store.create_part(
        description="0603 100nF X7R",
        category="capacitor",
        mpn=None,
        quantity_on_hand=0,
        min_quantity=50,
    )
    return a, b, c


def test_catalogue_renders(client) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_empty_catalogue_says_so(client) -> None:
    """An empty table with no explanation looks broken, not empty."""
    body = client.get("/").text

    assert "No parts" in body


def test_catalogue_lists_every_part(client, store) -> None:
    _seed(store)

    body = client.get("/").text

    assert "IRF7910" in body
    assert "TPS51980" in body
    assert "0603 100nF X7R" in body


def test_catalogue_shows_what_you_scan_for(client, store) -> None:
    """MPN, description, location, and on-hand vs min — the bench-side read."""
    _seed(store)

    body = client.get("/").text

    assert "A3" in body
    assert "Dual N-channel MOSFET" in body
    # count and threshold both visible; the exact rendering is styling's call
    assert "10" in body and "4" in body


def test_rows_carry_their_status_class(client, store) -> None:
    """The colour comes from `Part.status`, exposed as a class the CSS keys on.

    Asserting the class rather than the colour keeps this test honest across
    styling changes — the styling pass can change what amber looks like, but
    not whether a low part is marked low.
    """
    _seed(store)

    body = client.get("/").text

    assert "status-ok" in body
    assert "status-low" in body
    assert "status-out" in body


def test_a_part_without_an_mpn_still_renders(client, store) -> None:
    """Salvaged parts have no MPN. The row must not show 'None'."""
    _seed(store)

    body = client.get("/").text

    assert "None" not in body


def test_category_shows_its_label_not_its_slug(client, store) -> None:
    """The stored value is `transistor_bjt`; a person reads `Transistor (BJT)`."""
    store.create_part(description="NPN", category="transistor_bjt", mpn="2N3904")

    body = client.get("/").text

    assert "Transistor (BJT)" in body
    assert "transistor_bjt" not in body.replace('value="transistor_bjt"', "")


# --- search and filter ------------------------------------------------------


def test_search_by_mpn_is_case_insensitive(client, store) -> None:
    _seed(store)

    body = client.get("/", params={"q": "irf7910"}).text

    assert "IRF7910" in body
    assert "TPS51980" not in body


def test_search_matches_a_substring(client, store) -> None:
    """You remember 'IRF' and half a number, not the whole MPN."""
    _seed(store)

    body = client.get("/", params={"q": "7910"}).text

    assert "IRF7910" in body


def test_search_matches_the_description(client, store) -> None:
    _seed(store)

    body = client.get("/", params={"q": "buck"}).text

    assert "TPS51980" in body
    assert "IRF7910" not in body


def test_search_with_no_match_says_so(client, store) -> None:
    _seed(store)

    body = client.get("/", params={"q": "nothing-like-this"}).text

    assert "No parts" in body


def test_search_box_keeps_the_query(client, store) -> None:
    """After searching, the box still shows what you typed."""
    _seed(store)

    body = client.get("/", params={"q": "buck"}).text

    assert 'value="buck"' in body


def test_filter_by_category(client, store) -> None:
    _seed(store)

    body = client.get("/", params={"category": "ic"}).text

    assert "TPS51980" in body
    assert "IRF7910" not in body


def test_unknown_category_is_a_400_not_an_empty_shelf(client, store) -> None:
    _seed(store)

    response = client.get("/", params={"category": "mosfets"})

    assert response.status_code == 400


def test_search_and_filter_combine(client, store) -> None:
    _seed(store)
    store.create_part(description="Another MOSFET", category="mosfet", mpn="AO3400")

    body = client.get("/", params={"q": "mosfet", "category": "mosfet"}).text

    assert "IRF7910" in body
    assert "AO3400" in body
    assert "TPS51980" not in body


# --- navigation -------------------------------------------------------------


@pytest.mark.parametrize("target", ["/order", "/parts/new"])
def test_nav_links_to_the_other_screens(client, target) -> None:
    """The nav is how a tablet gets around — no URL bar typing."""
    body = client.get("/").text

    assert f'href="{target}"' in body


def test_each_row_links_to_the_part(client, store) -> None:
    a, _, _ = _seed(store)

    body = client.get("/").text

    assert f'href="/parts/{a.id}"' in body


# --- the store's search, tested directly -----------------------------------


def test_store_search_by_query_and_category(store) -> None:
    _seed(store)

    assert [p.mpn for p in store.search(q="IRF")] == ["IRF7910"]
    assert [p.mpn for p in store.search(category="ic")] == ["TPS51980"]
    assert [p.mpn for p in store.search(q="buck", category="ic")] == ["TPS51980"]
    assert store.search(q="buck", category="mosfet") == []


def test_store_search_with_nothing_returns_everything(store) -> None:
    _seed(store)

    assert len(store.search()) == 3


def test_store_search_rejects_unknown_category(store) -> None:
    with pytest.raises(ValueError):
        store.search(category="mosfets")


def test_store_search_treats_like_wildcards_literally(store) -> None:
    """A search for "100%" must not match every part — the % is escaped."""
    store.create_part(description="100% tin solder", category="consumable", mpn="SN100")
    store.create_part(description="Something else", category="other", mpn="X_1")

    assert [p.mpn for p in store.search(q="100%")] == ["SN100"]
    assert [p.mpn for p in store.search(q="_1")] == ["X_1"]
    # A bare "%" finds the one part whose text literally contains one — not all of them.
    assert [p.mpn for p in store.search(q="%")] == ["SN100"]
