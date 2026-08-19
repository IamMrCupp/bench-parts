"""The `project` field — one catalogue, several kinds of stock."""

import pytest
from fastapi.testclient import TestClient

from bench_parts.api import PART_KEYS
from bench_parts.app import app
from bench_parts.auth import API_TOKEN_ENV_VAR
from bench_parts.schema import SCHEMA_VERSION

TOKEN = "test-api-token-not-a-real-one"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv(API_TOKEN_ENV_VAR, TOKEN)


@pytest.fixture
def api(db_path):
    return TestClient(app)


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def seeded(store):
    a = store.create_part(
        description="WS2815 strip", category="led_opto", mpn="WS2815", project="Audiophore"
    )
    b = store.create_part(description="PETG Black", category="filament", project="3D printing")
    c = store.create_part(description="MOSFET", category="mosfet", mpn="IRF7910", project="bench")
    d = store.create_part(description="No project", category="other", mpn="NOPROJ")
    return a, b, c, d


def test_schema_is_at_least_v4() -> None:
    assert SCHEMA_VERSION >= 4


# --- store ------------------------------------------------------------------


def test_project_round_trips_and_is_trimmed(store) -> None:
    p = store.create_part(description="x", category="other", project="  Audiophore ")

    assert p.project == "Audiophore"


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_blank_project_is_none(store, blank) -> None:
    assert store.create_part(description="x", category="other", project=blank).project is None


def test_update_project(store, seeded) -> None:
    a, *_ = seeded
    assert store.update_part(a.id, project="Audiophore v2").project == "Audiophore v2"
    assert store.update_part(a.id, project="").project is None


def test_filter_by_project_is_case_insensitive(store, seeded) -> None:
    assert [p.mpn for p in store.search(project="audiophore")] == ["WS2815"]
    assert [p.mpn for p in store.search(project="AUDIOPHORE")] == ["WS2815"]


def test_filter_combines_with_category_and_query(store, seeded) -> None:
    store.create_part(
        description="Another Audiophore thing", category="other", mpn="AP2", project="Audiophore"
    )
    assert [p.mpn for p in store.search(project="Audiophore", category="led_opto")] == ["WS2815"]
    assert [p.mpn for p in store.search(project="Audiophore", q="another")] == ["AP2"]


def test_list_projects_is_distinct_sorted_and_live_only(store, seeded) -> None:
    store.create_part(description="dup", category="other", project="audiophore")  # different case
    retired = store.create_part(description="old", category="other", project="Old project")
    store.retire_part(retired.id)

    assert store.list_projects() == ["3D printing", "Audiophore", "bench"]


# --- UI ---------------------------------------------------------------------


def test_catalogue_filters_by_project(client, seeded) -> None:
    body = client.get("/", params={"project": "Audiophore"}).text

    assert "WS2815" in body
    assert "IRF7910" not in body
    assert "PETG Black" not in body


def test_catalogue_offers_a_project_select_from_existing_projects(client, seeded) -> None:
    body = client.get("/").text

    assert 'name="project"' in body
    for name in ("Audiophore", "3D printing", "bench"):
        assert f'value="{name}"' in body


def test_catalogue_rows_show_the_project_chip(client, seeded) -> None:
    body = client.get("/").text

    assert 'class="chip project"' in body
    assert ">Audiophore<" in body


def test_catalogue_project_select_keeps_selection(client, seeded) -> None:
    body = client.get("/", params={"project": "bench"}).text

    assert 'value="bench" selected' in body


def test_add_form_has_a_project_field_with_datalist(client, seeded) -> None:
    body = client.get("/parts/new").text

    assert 'name="project"' in body
    assert "<datalist" in body
    assert 'value="Audiophore"' in body


def test_add_part_with_project(client, store) -> None:
    client.post(
        "/parts",
        data={"description": "x", "category": "other", "mpn": "NEW", "project": " Audiophore "},
    )

    assert store.find_by_mpn("NEW")[0].project == "Audiophore"


def test_edit_form_prefills_project_and_can_change_it(client, store, seeded) -> None:
    a, *_ = seeded
    assert 'name="project" value="Audiophore"' in client.get(f"/parts/{a.id}").text

    client.post(
        f"/parts/{a.id}",
        data={
            "description": "WS2815 strip",
            "category": "led_opto",
            "mpn": "WS2815",
            "project": "bench",
        },
    )

    assert store.get_part(a.id).project == "bench"


def test_detail_shows_project(client, seeded) -> None:
    a, *_ = seeded
    assert ">Audiophore<" in client.get(f"/parts/{a.id}").text


def test_order_page_shows_project_chip(client, store) -> None:
    store.create_part(
        description="Low thing",
        category="fuse",
        mpn="F1",
        quantity_on_hand=0,
        min_quantity=2,
        project="Audiophore",
    )

    assert ">Audiophore<" in client.get("/order").text


# --- API --------------------------------------------------------------------


def test_project_in_json(api, seeded) -> None:
    a, *_ = seeded
    assert "project" in PART_KEYS
    assert api.get(f"/api/parts/{a.id}", headers=_auth()).json()["project"] == "Audiophore"


def test_api_filter_by_project(api, seeded) -> None:
    body = api.get("/api/parts", params={"project": "audiophore"}, headers=_auth()).json()

    assert [p["mpn"] for p in body] == ["WS2815"]


def test_api_lists_projects(api, seeded) -> None:
    assert api.get("/api/projects", headers=_auth()).json() == [
        "3D printing",
        "Audiophore",
        "bench",
    ]


def test_api_below_min_can_filter_by_project(api, store) -> None:
    store.create_part(
        description="a",
        category="fuse",
        mpn="A",
        quantity_on_hand=0,
        min_quantity=1,
        project="Audiophore",
    )
    store.create_part(
        description="b",
        category="fuse",
        mpn="B",
        quantity_on_hand=0,
        min_quantity=1,
        project="bench",
    )

    body = api.get(
        "/api/parts", params={"below_min": "1", "project": "bench"}, headers=_auth()
    ).json()

    assert [p["mpn"] for p in body] == ["B"]
