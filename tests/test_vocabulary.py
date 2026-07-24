"""The controlled vocabularies the schema constrains itself to.

`Category` and `MovementReason` are the single definition of their respective
value sets — the schema's CHECK constraints are *generated* from them (see
`bench_parts.schema`) rather than hand-listed alongside them. That's deliberate:
intake hand-listed its statuses in both places and needed a drift test to keep
them honest. Deriving removes the failure mode instead of detecting it.
"""

from bench_parts.vocabulary import Category, MovementReason, sql_in_list


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
