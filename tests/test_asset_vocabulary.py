"""The asset vocabularies the schema constrains itself to.

`AssetType`, `AssetStatus` and `AssetAction` are the single definition of their
value sets — the `assets` / `asset_movements` CHECK constraints are *generated*
from them via `sql_in_list`, the same drift-proof arrangement the parts
vocabulary uses. These tests pin the slugs (rows carry them), the labels, and
the boundary coercion that turns HTTP strings into real members.
"""

import pytest

from bench_parts.vocabulary import (
    AssetAction,
    AssetStatus,
    AssetType,
    sql_in_list,
    to_asset_action,
    to_asset_status,
    to_asset_type,
)


def test_values_are_slugs() -> None:
    for member in [*AssetType, *AssetStatus, *AssetAction]:
        assert member.value.islower()
        assert " " not in member.value
        assert "/" not in member.value


def test_every_member_has_a_label() -> None:
    for member in [*AssetType, *AssetStatus, *AssetAction]:
        assert member.label


def test_type_labels_are_unique() -> None:
    labels = [t.label for t in AssetType]
    assert len(labels) == len(set(labels))


def test_v1_asset_types_present() -> None:
    """The bench-recon v1 kit. Extending is a one-line change in vocabulary.py."""
    values = {t.value for t in AssetType}
    assert {
        "sdr",
        "logic_analyzer",
        "debugger",
        "can_interface",
        "flash_programmer",
        "chip_clip",
        "flipper_zero",
        "proxmark",
        "level_shifter",
        "other",
    } <= values


def test_asset_statuses() -> None:
    assert {s.value for s in AssetStatus} == {
        "available",
        "assigned",
        "out_for_repair",
        "retired",
    }


def test_asset_actions() -> None:
    assert {a.value for a in AssetAction} == {
        "acquire",
        "assign",
        "return",
        "repair_out",
        "repair_in",
        "retire",
        "unretire",
    }


def test_sql_in_list_covers_the_whole_enum() -> None:
    """A member added to any asset enum cannot be missed by the generated SQL."""
    for enum_cls in (AssetType, AssetStatus, AssetAction):
        rendered = sql_in_list(enum_cls)
        for member in enum_cls:
            assert f"'{member.value}'" in rendered


# --- boundary coercion (the StrEnum `is` vs `==` trap, same as parts) --------


def test_coercers_accept_plain_strings() -> None:
    assert to_asset_type("sdr") is AssetType.SDR
    assert to_asset_status("assigned") is AssetStatus.ASSIGNED
    assert to_asset_action("return") is AssetAction.RETURN


def test_coercers_accept_members() -> None:
    assert to_asset_type(AssetType.DEBUGGER) is AssetType.DEBUGGER


def test_coercion_is_case_and_space_insensitive() -> None:
    assert to_asset_type("  SDR ") is AssetType.SDR


def test_unknown_value_is_rejected_and_names_options() -> None:
    with pytest.raises(ValueError, match="sdr"):
        to_asset_type("radio")


@pytest.mark.parametrize("value", [None, ""])
def test_empty_is_rejected(value) -> None:
    with pytest.raises(ValueError):
        to_asset_type(value)


def test_label_is_not_an_accepted_slug() -> None:
    with pytest.raises(ValueError):
        to_asset_type("Debugger (JTAG/SWD/UART)")
