"""Controlled vocabularies for parts, stock movements, and durable assets.

These enums are the **single definition** of their value sets. The schema's
CHECK constraints are generated from them via :func:`sql_in_list` rather than
hand-listed a second time — benchhud-intake hand-listed its statuses in both
places and needed a drift test to keep the two honest. Deriving removes the
failure mode instead of detecting it.

The **asset** vocabularies (:class:`AssetType`, :class:`AssetStatus`,
:class:`AssetAction`) live here too, beside the parts ones, so the same coercion
and ``sql_in_list`` machinery backs both. Assets are durable, serialized test
gear (an SDR, a debugger) — *assigned* to an engagement and returned, never
consumed — and are a separate model from consumable :class:`Category` parts.

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
    # v2 (2026-08-19): bench-parts tracks all maker parts, not just board-repair
    # components. Adding a member here needs a schema migration — the category
    # CHECK is generated from this enum at table creation and SQLite can't ALTER
    # a CHECK, so schema.py rebuilds the table. See _upgrade_to_v2 for the shape.
    FILAMENT = "filament"
    MODULE = "module"
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
    Category.FILAMENT: "Filament",
    Category.MODULE: "Module / Board",
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


class AssetType(StrEnum):
    """What kind of durable test instrument an asset is.

    A fixed list, same reasoning as :class:`Category`: free text fragments the
    registry and makes the HUD's "what gear is on this engagement" grouping
    unreliable. Extending is a one-line change here; the ``assets`` CHECK follows
    automatically. The v1 set is the bench-recon hardware/automotive/IoT kit.
    """

    SDR = "sdr"  # software-defined radio: HackRF, RTL-SDR
    LOGIC_ANALYZER = "logic_analyzer"
    DEBUGGER = "debugger"  # JTAG/SWD/UART: J-Link, ST-Link, Bus Pirate
    CAN_INTERFACE = "can_interface"  # CAN / OBD-II: CANable, OBD-II dongles
    FLASH_PROGRAMMER = "flash_programmer"  # CH341A, TL866 / minipro
    CHIP_CLIP = "chip_clip"  # SOIC / SOP test clips
    FLIPPER_ZERO = "flipper_zero"
    PROXMARK = "proxmark"
    LEVEL_SHIFTER = "level_shifter"  # interface / level-shifter boards
    OTHER = "other"

    @property
    def label(self) -> str:
        """Display text for the UI."""
        return _ASSET_TYPE_LABELS[self]


_ASSET_TYPE_LABELS: dict[AssetType, str] = {
    AssetType.SDR: "SDR",
    AssetType.LOGIC_ANALYZER: "Logic analyzer",
    AssetType.DEBUGGER: "Debugger (JTAG/SWD/UART)",
    AssetType.CAN_INTERFACE: "CAN / OBD-II interface",
    AssetType.FLASH_PROGRAMMER: "Flash programmer",
    AssetType.CHIP_CLIP: "Chip clip (SOIC/SOP)",
    AssetType.FLIPPER_ZERO: "Flipper Zero",
    AssetType.PROXMARK: "Proxmark",
    AssetType.LEVEL_SHIFTER: "Level shifter / interface board",
    AssetType.OTHER: "Other",
}


class AssetStatus(StrEnum):
    """Where a durable asset is in its lifecycle.

    The cache on the ``assets`` row; the ``asset_movements`` ledger is the source
    of truth it is derived from (see :mod:`bench_parts.asset_store`). ``assigned``
    is the only status that carries an engagement ref.
    """

    AVAILABLE = "available"
    ASSIGNED = "assigned"
    OUT_FOR_REPAIR = "out_for_repair"
    RETIRED = "retired"

    @property
    def label(self) -> str:
        return _ASSET_STATUS_LABELS[self]


_ASSET_STATUS_LABELS: dict[AssetStatus, str] = {
    AssetStatus.AVAILABLE: "Available",
    AssetStatus.ASSIGNED: "Assigned",
    AssetStatus.OUT_FOR_REPAIR: "Out for repair",
    AssetStatus.RETIRED: "Retired",
}


class AssetAction(StrEnum):
    """A row in the append-only asset ledger — one lifecycle transition.

    ``acquire`` is the opening row written when an asset is first registered, so
    that (exactly like :attr:`MovementReason.INITIAL` for parts) no path changes
    an asset's status without leaving a ledger row. Every action maps to a single
    resulting ``(status, assigned_ref)``, which is what makes the cached status
    rebuildable from the log.
    """

    ACQUIRE = "acquire"  # registered into the catalogue → available
    ASSIGN = "assign"  # → assigned (to an engagement ref)
    RETURN = "return"  # → available
    REPAIR_OUT = "repair_out"  # → out_for_repair
    REPAIR_IN = "repair_in"  # → available
    RETIRE = "retire"  # → retired
    UNRETIRE = "unretire"  # → available

    @property
    def label(self) -> str:
        return _ASSET_ACTION_LABELS[self]


_ASSET_ACTION_LABELS: dict[AssetAction, str] = {
    AssetAction.ACQUIRE: "Acquired",
    AssetAction.ASSIGN: "Assigned",
    AssetAction.RETURN: "Returned",
    AssetAction.REPAIR_OUT: "Sent for repair",
    AssetAction.REPAIR_IN: "Back from repair",
    AssetAction.RETIRE: "Retired",
    AssetAction.UNRETIRE: "Unretired",
}


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


def to_asset_type(value: object) -> AssetType:
    """Coerce caller input to an :class:`AssetType` (stored slug, any case)."""
    return _coerce(AssetType, value, "asset type")  # type: ignore[return-value]


def to_asset_status(value: object) -> AssetStatus:
    """Coerce caller input to an :class:`AssetStatus`."""
    return _coerce(AssetStatus, value, "asset status")  # type: ignore[return-value]


def to_asset_action(value: object) -> AssetAction:
    """Coerce caller input to an :class:`AssetAction`."""
    return _coerce(AssetAction, value, "asset action")  # type: ignore[return-value]


def sql_in_list(enum_cls: type[StrEnum]) -> str:
    """Render an enum as a quoted, sorted list for a SQL ``IN (…)`` clause.

    Sorted so the generated DDL is stable: an unstable ordering would make the
    schema text churn between runs for no reason.
    """
    return ", ".join(f"'{member.value}'" for member in sorted(enum_cls, key=lambda m: m.value))
