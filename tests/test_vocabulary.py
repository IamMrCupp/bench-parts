"""The controlled vocabularies the schema constrains itself to.

`Category` and `MovementReason` are the single definition of their respective
value sets — the schema's CHECK constraints are *generated* from them (see
`bench_parts.schema`) rather than hand-listed alongside them. That's deliberate:
intake hand-listed its statuses in both places and needed a drift test to keep
them honest. Deriving removes the failure mode instead of detecting it.
"""

import pytest

from bench_parts.vocabulary import (
    Category,
    MovementReason,
    sql_in_list,
    to_category,
    to_reason,
)


def test_categories_are_slugs() -> None:
    """Stored values are slugs, not display text.

    The UI label is presentation and can be reworded freely; the stored value
    can't be, because rows already carry it.
    """
    for category in Category:
        assert category.value.islower()
        assert " " not in category.value
        assert "/" not in category.value


def test_every_category_has_a_label() -> None:
    for category in Category:
        assert category.label
        assert category.label != category.value or category.value == "other"


def test_labels_are_unique() -> None:
    labels = [c.label for c in Category]

    assert len(labels) == len(set(labels))


def test_expected_categories_are_present() -> None:
    """The starter set from plan.md Phase 1. Extending is a one-line change."""
    values = {c.value for c in Category}

    assert {"mosfet", "ic", "capacitor", "resistor", "connector", "other"} <= values


def test_movement_reasons() -> None:
    assert {r.value for r in MovementReason} == {
        "initial",
        "restock",
        "consume",
        "correction",
    }


def test_sql_in_list_quotes_every_member() -> None:
    """What the schema interpolates into its CHECK constraints."""
    rendered = sql_in_list(MovementReason)

    assert rendered == "'consume', 'correction', 'initial', 'restock'"


def test_sql_in_list_covers_the_whole_enum() -> None:
    """A member added to the enum cannot be missed by the generated SQL."""
    rendered = sql_in_list(Category)

    for category in Category:
        assert f"'{category.value}'" in rendered


# --- boundary coercion ------------------------------------------------------
#
# These exist because of benchhud-intake's C.3 bug: `Status` was a StrEnum, a
# validator compared with `is`, and string input silently skipped the check.
# 126 green tests said nothing, because every test passed enum members while
# every real caller passes strings off HTTP. `Category` has the same shape.


def test_to_category_accepts_a_plain_string() -> None:
    """The path every real caller takes — a form post or a JSON body."""
    assert to_category("mosfet") is Category.MOSFET


def test_to_category_accepts_an_enum_member() -> None:
    assert to_category(Category.MOSFET) is Category.MOSFET


def test_to_category_returns_an_identity_comparable_member() -> None:
    """The specific trap: `==` passes for a bare string where `is` fails.

    Coercion must yield a real member, so downstream code can use either
    comparison without a silent behaviour change.
    """
    coerced = to_category("capacitor")

    assert coerced is Category.CAPACITOR
    assert coerced == Category.CAPACITOR


def test_raw_string_is_not_identity_equal_to_the_member() -> None:
    """Pins *why* coercion is needed — delete this and the reason is invisible.

    Held in a variable rather than written as a literal, and that detail is the
    whole point: ruff's `F632` catches `is` against a *literal*, but intake's
    bug compared a variable that had arrived from a form. The linter could not
    have saved it, which is why the coercion boundary has to.
    """
    from_form = "capacitor"

    assert from_form == Category.CAPACITOR
    assert from_form is not Category.CAPACITOR


def test_to_category_is_case_and_space_insensitive() -> None:
    """Bench input is typed by a person; " MOSFET " should not be an error."""
    assert to_category(" MOSFET ") is Category.MOSFET


def test_to_category_rejects_an_unknown_value() -> None:
    with pytest.raises(ValueError, match="mosfets"):
        to_category("mosfets")


def test_to_category_rejects_a_label_rather_than_a_slug() -> None:
    """Labels are presentation. Accepting them would make the stored value
    depend on UI wording."""
    with pytest.raises(ValueError):
        to_category("Crystal / Oscillator")


@pytest.mark.parametrize("value", [None, ""])
def test_to_category_rejects_empty(value) -> None:
    with pytest.raises(ValueError):
        to_category(value)


def test_to_reason_accepts_a_plain_string() -> None:
    assert to_reason("restock") is MovementReason.RESTOCK


def test_to_reason_rejects_an_unknown_value() -> None:
    with pytest.raises(ValueError, match="shrinkage"):
        to_reason("shrinkage")


def test_error_names_the_valid_options() -> None:
    """A bad category at a bench should say what's allowed, not just "invalid"."""
    with pytest.raises(ValueError) as excinfo:
        to_category("widget")

    assert "mosfet" in str(excinfo.value)
