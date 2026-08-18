"""Turning form strings into what the store wants.

A browser posts every field as text, and an empty field as ``""``. The store
already normalises blank text to ``None`` and coerces category strings; what it
does *not* do is parse numbers or money — those are presentation concerns, and
they live here so the route handlers stay thin and the conversions are
testable on their own.

Errors are ``ValueError`` with a message written for the person at the bench,
never a bare ``int()`` traceback.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def parse_count(value: str | None, field: str, *, allow_zero: bool = True) -> int:
    """A whole non-negative number.

    With ``allow_zero`` (the default), blank means zero — "none yet" is a
    normal answer for an opening count or a threshold. For a stock *movement*
    pass ``allow_zero=False``: moving nothing is a mistake, and a blank amount
    should say so rather than silently succeed.
    """
    text = (value or "").strip()
    if not text:
        if allow_zero:
            return 0
        raise ValueError(f"{field} is required")
    try:
        number = int(text)
    except ValueError:
        raise ValueError(f"{field} must be a whole number, got {text!r}") from None
    if number < 0:
        raise ValueError(f"{field} cannot be negative")
    if number == 0 and not allow_zero:
        raise ValueError(f"{field} must be at least 1")
    return number


def parse_signed_count(value: str | None, field: str) -> int:
    """A whole number that may be negative — a correction goes either way.
    Zero is rejected: a correction that corrects nothing is a mistake."""
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field} is required — use a positive or negative whole number")
    try:
        number = int(text)
    except ValueError:
        raise ValueError(f"{field} must be a whole number like 3 or -2, got {text!r}") from None
    if number == 0:
        raise ValueError(f"{field} of 0 changes nothing")
    return number


def parse_dollars_to_cents(value: str | None, field: str = "unit cost") -> int | None:
    """``"1.87"`` → ``187``. Blank → ``None`` (plenty of salvaged parts have no
    meaningful price).

    Accepts a leading ``$`` and thousands commas, because that's how a price
    gets pasted from a supplier page. Rounds to the nearest cent rather than
    rejecting sub-cent input — a price list saying ``$0.0187 each`` is real.
    The store never sees the float; it gets an ``int``.
    """
    text = (value or "").strip().lstrip("$").replace(",", "")
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"{field} must be a price like 1.87, got {value!r}") from None
    if amount < 0:
        raise ValueError(f"{field} cannot be negative")
    return int((amount * 100).to_integral_value())


def cents_to_dollars_field(cents: int | None) -> str:
    """The reverse, for pre-filling an edit form. ``187`` → ``"1.87"``; ``None`` → ``""``."""
    if cents is None:
        return ""
    return f"{cents / 100:.2f}"
