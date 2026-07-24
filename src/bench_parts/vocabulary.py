"""Controlled vocabularies for parts and stock movements.

These enums are the **single definition** of their value sets. The schema's
CHECK constraints are generated from them via :func:`sql_in_list` rather than
hand-listed a second time — benchhud-intake hand-listed its statuses in both
places and needed a drift test to keep the two honest. Deriving removes the
failure mode instead of detecting it.

Stored values are slugs; :attr:`Category.label` carries the display text. The
label is presentation and can be reworded freely, but the stored value can't be
— rows already carry it, and changing one means a migration.
"""

from __future__ import annotations

from enum import StrEnum


class Category(StrEnum):
    """What kind of part this is.

    A fixed list rather than free text: free text fragments a catalogue into
    "cap" / "capacitor" / "Capacitor" and makes filtering — and the eventual HUD
    grouping — unreliable. Extending is a one-line change here; the schema
    constraint follows automatically.
    """

    MOSFET = "mosfet"
    TRANSISTOR_BJT = "transistor_bjt"
    IC = "ic"
    DIODE = "diode"
    CAPACITOR = "capacitor"
    RESISTOR = "resistor"
    INDUCTOR = "inductor"
    CRYSTAL_OSCILLATOR = "crystal_oscillator"
    CONNECTOR = "connector"
    FUSE = "fuse"
    RELAY = "relay"
    SWITCH = "switch"
    LED_OPTO = "led_opto"
    TRANSFORMER = "transformer"
    CABLE_WIRE = "cable_wire"
    HARDWARE_MECHANICAL = "hardware_mechanical"
    CONSUMABLE = "consumable"
    OTHER = "other"

    @property
    def label(self) -> str:
        """Display text for the UI."""
        return _CATEGORY_LABELS[self]


_CATEGORY_LABELS: dict[Category, str] = {
    Category.MOSFET: "MOSFET",
    Category.TRANSISTOR_BJT: "Transistor (BJT)",
    Category.IC: "IC",
    Category.DIODE: "Diode",
    Category.CAPACITOR: "Capacitor",
    Category.RESISTOR: "Resistor",
    Category.INDUCTOR: "Inductor",
    Category.CRYSTAL_OSCILLATOR: "Crystal / Oscillator",
    Category.CONNECTOR: "Connector",
    Category.FUSE: "Fuse",
    Category.RELAY: "Relay",
    Category.SWITCH: "Switch",
    Category.LED_OPTO: "LED / Opto",
    Category.TRANSFORMER: "Transformer",
    Category.CABLE_WIRE: "Cable / Wire",
    Category.HARDWARE_MECHANICAL: "Hardware / Mechanical",
    Category.CONSUMABLE: "Consumable",
    Category.OTHER: "Other",
}


class MovementReason(StrEnum):
    """Why a stock level changed.

    ``initial`` is the opening count recorded when a part is first added — it's
    a movement like any other, so that no path exists which changes a quantity
    without leaving a row in the log.
    """

    INITIAL = "initial"
    RESTOCK = "restock"
    CONSUME = "consume"
    CORRECTION = "correction"


def _coerce(enum_cls: type[StrEnum], value: object, what: str) -> StrEnum:
    """Turn caller input into a real enum member, or raise.

    **Why this exists.** These are ``StrEnum``s, so a bare string compares equal
    to a member (``"consume" == MovementReason.CONSUME``) but is not identical
    to one (``"consume" is not MovementReason.CONSUME``). benchhud-intake was
    bitten by exactly this: a validator compared with ``is``, so string input
    silently skipped its check, and 126 passing tests never caught it because
    every test passed enum members while every real caller passes strings off
    HTTP.

    Coercing at the boundary means downstream code holds real members and can
    use either comparison safely. Do it *once*, here, rather than trusting every
    call site to remember.
    """
    if isinstance(value, enum_cls):
        return value

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} is required — one of: {_options(enum_cls)}")

    try:
        return enum_cls(value.strip().lower())
    except ValueError:
        raise ValueError(
            f"unknown {what} {value!r} — expected one of: {_options(enum_cls)}"
        ) from None


def _options(enum_cls: type[StrEnum]) -> str:
    return ", ".join(sorted(m.value for m in enum_cls))


def to_category(value: object) -> Category:
    """Coerce caller input to a :class:`Category`.

    Accepts the stored slug in any case, with surrounding whitespace — bench
    input is typed by a person. Does **not** accept display labels: those are
    presentation, and accepting them would tie stored values to UI wording.
    """
    return _coerce(Category, value, "category")  # type: ignore[return-value]


def to_reason(value: object) -> MovementReason:
    """Coerce caller input to a :class:`MovementReason`."""
    return _coerce(MovementReason, value, "reason")  # type: ignore[return-value]


def sql_in_list(enum_cls: type[StrEnum]) -> str:
    """Render an enum as a quoted, sorted list for a SQL ``IN (…)`` clause.

    Sorted so the generated DDL is stable: an unstable ordering would make the
    schema text churn between runs for no reason.
    """
    return ", ".join(f"'{member.value}'" for member in sorted(enum_cls, key=lambda m: m.value))
