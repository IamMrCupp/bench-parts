"""Stock status is derived from the counts, never stored.

One definition, read by both Phase 2's colour-coding and the "to order" query.
Reimplementing the comparison in either place is how the list and the colours
end up disagreeing about the same part — which is exactly the bug a person
would trust the wrong way round at a bench.

Mirrors intake's `OPEN_STATUSES` lesson: derive the set so a newly added state
can't silently go unclassified.
"""

import pytest

from bench_parts.stock import StockStatus, is_below_min, stock_status


def test_zero_is_out() -> None:
    assert stock_status(0, 5) is StockStatus.OUT


def test_below_min_is_low() -> None:
    assert stock_status(2, 5) is StockStatus.LOW


def test_exactly_min_is_low() -> None:
    """The boundary that decides whether a part makes the supplier run.

    "At or below" — a part sitting exactly on its threshold is one job away
    from being out, so it belongs on the list.
    """
    assert stock_status(5, 5) is StockStatus.LOW


def test_one_above_min_is_ok() -> None:
    """The other side of the same boundary, so neither can drift alone."""
    assert stock_status(6, 5) is StockStatus.OK


def test_zero_min_means_never_low() -> None:
    """`min_quantity = 0` reads as "don't flag this one" — only an actual zero
    count is interesting."""
    assert stock_status(1, 0) is StockStatus.OK


def test_zero_min_and_zero_quantity_is_still_out() -> None:
    """Out is out, regardless of whether you wanted a threshold."""
    assert stock_status(0, 0) is StockStatus.OUT


@pytest.mark.parametrize(
    ("quantity", "minimum", "expected"),
    [
        (0, 0, StockStatus.OUT),
        (0, 3, StockStatus.OUT),
        (1, 3, StockStatus.LOW),
        (3, 3, StockStatus.LOW),
        (4, 3, StockStatus.OK),
        (100, 0, StockStatus.OK),
    ],
)
def test_status_table(quantity, minimum, expected) -> None:
    assert stock_status(quantity, minimum) is expected


def test_negative_quantity_is_rejected() -> None:
    """The schema forbids it; this is the Python-side echo of the same rule."""
    with pytest.raises(ValueError):
        stock_status(-1, 0)


def test_negative_min_is_rejected() -> None:
    with pytest.raises(ValueError):
        stock_status(1, -1)


# --- the "to order" predicate ----------------------------------------------


@pytest.mark.parametrize(
    ("quantity", "minimum", "expected"),
    [
        (0, 0, True),
        (0, 3, True),
        (3, 3, True),
        (4, 3, False),
        (1, 0, False),
    ],
)
def test_is_below_min(quantity, minimum, expected) -> None:
    assert is_below_min(quantity, minimum) is expected


def test_below_min_agrees_with_status() -> None:
    """The predicate and the status must never disagree.

    C.4's query and Phase 2's colours are the two readers; if these two ever
    diverge, a part shows amber but never appears on the list to order.
    """
    for quantity in range(0, 8):
        for minimum in range(0, 8):
            expected = stock_status(quantity, minimum) in (StockStatus.OUT, StockStatus.LOW)

            assert is_below_min(quantity, minimum) is expected


def test_every_status_is_classified_as_orderable_or_not() -> None:
    """If a fourth status is added, this fails until it's classified.

    Intake's lesson: a state nobody classified looks exactly like a state that
    was lost.
    """
    for status in StockStatus:
        assert status in StockStatus.ORDERABLE or status is StockStatus.OK
