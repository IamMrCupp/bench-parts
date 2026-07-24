"""Part creation, lookup and update.

Every test here passes **strings** where a real caller would — a form post and a
JSON body both arrive as text. Intake learned this the hard way: its store was
exercised entirely with enum members, so 126 green tests said nothing about the
path every actual caller took.
"""

import sqlite3

import pytest

from bench_parts.db import connect
from bench_parts.schema import initialise
from bench_parts.stock import StockStatus
from bench_parts.store import PartStore
from bench_parts.vocabulary import Category, MovementReason


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    with connect(tmp_path / "parts.db") as conn:
        initialise(conn)
        yield PartStore(conn)


def _create(store: PartStore, **overrides):
    values = {
        "description": "Dual N-channel MOSFET",
        "category": "mosfet",
        "mpn": "IRF7910",
    }
    values.update(overrides)
    return store.create_part(**values)


# --- creation ---------------------------------------------------------------


def test_create_returns_a_part_with_an_id(store) -> None:
    part = _create(store)

    assert part.id is not None
    assert part.description == "Dual N-channel MOSFET"


def test_create_coerces_a_string_category(store) -> None:
    """The path every real caller takes."""
    part = _create(store, category="capacitor")

    assert part.category is Category.CAPACITOR


def test_create_accepts_an_enum_category(store) -> None:
    part = _create(store, category=Category.DIODE)

    assert part.category is Category.DIODE


def test_create_rejects_an_unknown_category(store) -> None:
    with pytest.raises(ValueError, match="widget"):
        _create(store, category="widget")


def test_create_rejects_a_blank_description(store) -> None:
    """A part you can't identify on a shelf isn't a record worth keeping."""
    with pytest.raises(ValueError):
        _create(store, description="   ")


def test_create_defaults_quantities_to_zero(store) -> None:
    part = _create(store)

    assert part.quantity_on_hand == 0
    assert part.min_quantity == 0


def test_create_with_opening_stock_writes_an_initial_movement(store) -> None:
    """No path may change a quantity without leaving a row in the log.

    This is the invariant the whole design rests on, laid down at the very
    first write rather than bolted on with the movement API in C.2.
    """
    part = _create(store, quantity_on_hand=12)

    movements = store.list_movements(part.id)

    assert part.quantity_on_hand == 12
    assert len(movements) == 1
    assert movements[0].delta == 12
    assert movements[0].reason is MovementReason.INITIAL


def test_create_without_opening_stock_writes_no_movement(store) -> None:
    """Zero isn't a movement — `delta != 0` is a schema constraint."""
    part = _create(store, quantity_on_hand=0)

    assert store.list_movements(part.id) == []


def test_create_rejects_negative_opening_stock(store) -> None:
    with pytest.raises(ValueError):
        _create(store, quantity_on_hand=-1)


def test_create_rejects_negative_min_quantity(store) -> None:
    with pytest.raises(ValueError):
        _create(store, min_quantity=-1)


def test_create_rejects_negative_unit_cost(store) -> None:
    with pytest.raises(ValueError):
        _create(store, unit_cost=-5)


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_blank_optional_fields_normalise_to_none(store, blank) -> None:
    """A form posts '' where a script posts None. The store shouldn't care."""
    part = _create(store, mpn=blank, manufacturer=blank, location=blank, notes=blank)

    assert part.mpn is None
    assert part.manufacturer is None
    assert part.location is None
    assert part.notes is None


def test_text_fields_are_stripped(store) -> None:
    part = _create(store, mpn="  IRF7910  ", location=" A3 ")

    assert part.mpn == "IRF7910"
    assert part.location == "A3"


def test_timestamps_are_set_on_create(store) -> None:
    part = _create(store)

    assert part.created_at
    assert part.updated_at == part.created_at


# --- reading ----------------------------------------------------------------


def test_get_part_round_trips_every_field(store) -> None:
    created = _create(
        store,
        mpn="TPS51980",
        manufacturer="TI",
        description="Buck controller",
        category="ic",
        quantity_on_hand=4,
        min_quantity=2,
        unit_cost=349,
        supplier="Digi-Key",
        supplier_url="https://example.invalid/tps51980",
        location="B7",
        notes="dual rail",
    )

    fetched = store.get_part(created.id)

    assert fetched == created


def test_get_part_returns_none_when_missing(store) -> None:
    assert store.get_part(9999) is None


def test_find_by_mpn_is_case_insensitive(store) -> None:
    """You're squinting at a chip through a loupe; case is not a search term."""
    _create(store, mpn="IRF7910")

    found = store.find_by_mpn("irf7910")

    assert len(found) == 1


def test_find_by_mpn_returns_every_match(store) -> None:
    """The same MPN legitimately spans manufacturers — see the schema comment."""
    _create(store, mpn="IRF7910", manufacturer="Infineon")
    _create(store, mpn="IRF7910", manufacturer="Vishay")

    found = store.find_by_mpn("IRF7910")

    assert len(found) == 2
    assert {p.manufacturer for p in found} == {"Infineon", "Vishay"}


def test_find_by_mpn_returns_empty_for_no_match(store) -> None:
    assert store.find_by_mpn("nothing-like-this") == []


def test_list_parts_returns_everything(store) -> None:
    _create(store, mpn="A")
    _create(store, mpn="B")

    assert len(store.list_parts()) == 2


def test_list_parts_filters_by_category(store) -> None:
    _create(store, mpn="A", category="mosfet")
    _create(store, mpn="B", category="capacitor")

    found = store.list_parts(category="capacitor")

    assert len(found) == 1
    assert found[0].mpn == "B"


def test_list_parts_rejects_an_unknown_category_filter(store) -> None:
    """A typo'd filter must not silently return an empty catalogue."""
    with pytest.raises(ValueError):
        store.list_parts(category="mosfets")


# --- derived status ---------------------------------------------------------


@pytest.mark.parametrize(
    ("quantity", "minimum", "expected"),
    [(0, 2, StockStatus.OUT), (2, 2, StockStatus.LOW), (3, 2, StockStatus.OK)],
)
def test_part_exposes_derived_status(store, quantity, minimum, expected) -> None:
    """Read through `bench_parts.stock`, never stored as a column."""
    part = _create(store, quantity_on_hand=quantity, min_quantity=minimum)

    assert part.status is expected


def test_status_is_not_a_stored_column(store) -> None:
    """Guards against someone "optimising" it into the table later."""
    _create(store)

    columns = {row[1] for row in store._conn.execute("PRAGMA table_info(parts)")}

    assert "status" not in columns


# --- updating ---------------------------------------------------------------


def test_update_changes_a_field(store) -> None:
    part = _create(store, location="A3")

    updated = store.update_part(part.id, location="C9")

    assert updated.location == "C9"


def test_update_leaves_unmentioned_fields_alone(store) -> None:
    part = _create(store, location="A3", notes="keep me")

    updated = store.update_part(part.id, location="C9")

    assert updated.notes == "keep me"
    assert updated.mpn == part.mpn


def test_update_bumps_updated_at_but_not_created_at(store) -> None:
    part = _create(store)

    updated = store.update_part(part.id, location="C9", now="2026-08-01T00:00:00Z")

    assert updated.created_at == part.created_at
    assert updated.updated_at == "2026-08-01T00:00:00Z"


def test_update_coerces_a_string_category(store) -> None:
    part = _create(store, category="mosfet")

    updated = store.update_part(part.id, category="diode")

    assert updated.category is Category.DIODE


def test_update_rejects_an_unknown_category(store) -> None:
    part = _create(store)

    with pytest.raises(ValueError):
        store.update_part(part.id, category="widget")


def test_update_rejects_a_missing_part(store) -> None:
    with pytest.raises(LookupError):
        store.update_part(9999, location="C9")


def test_update_cannot_change_quantity_directly(store) -> None:
    """Quantity moves only through the movements log — that's C.2's job.

    Allowing it here would create a second path that changes stock without
    writing a movement, which is precisely the drift this design prevents.
    """
    part = _create(store, quantity_on_hand=5)

    with pytest.raises(ValueError, match="movement"):
        store.update_part(part.id, quantity_on_hand=99)


def test_update_can_change_min_quantity(store) -> None:
    """The threshold is a policy field, not a stock level — safe to edit."""
    part = _create(store, min_quantity=2)

    updated = store.update_part(part.id, min_quantity=10)

    assert updated.min_quantity == 10


def test_update_rejects_a_negative_min_quantity(store) -> None:
    part = _create(store)

    with pytest.raises(ValueError):
        store.update_part(part.id, min_quantity=-1)


def test_update_normalises_blank_to_none(store) -> None:
    part = _create(store, notes="something")

    updated = store.update_part(part.id, notes="   ")

    assert updated.notes is None


# --- schema-level backstop --------------------------------------------------


def test_store_rejects_before_the_database_has_to(store) -> None:
    """Validation happens in Python; the CHECK constraints are the backstop.

    If this ever raises IntegrityError instead of ValueError, validation has
    been bypassed somewhere.
    """
    with pytest.raises(ValueError):
        _create(store, min_quantity=-1)

    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO parts (description, category, quantity_on_hand, min_quantity,"
            " created_at, updated_at) VALUES ('x', 'mosfet', 0, -1, 'now', 'now')"
        )
