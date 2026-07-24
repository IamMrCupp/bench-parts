"""The movements log and the cached quantity that must never drift from it.

This is the file that earns the design. The claim "the log is the source of
truth and the cache is derived" is only true if a movement and its cache update
are atomic — so the interesting tests here are the ones that try to break that
pairing: a forced mid-write failure, and concurrent writers.

Intake *measured* its race rather than assuming it was fine (its naive MAX+1
produced 9 duplicate refs in 80 under 8 threads). Same treatment here.
"""

import sqlite3
import threading

import pytest

from bench_parts.db import connect
from bench_parts.schema import initialise
from bench_parts.store import PartStore
from bench_parts.vocabulary import MovementReason


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    path = tmp_path / "parts.db"
    with connect(path) as conn:
        initialise(conn)
    return path


@pytest.fixture
def store(db_file):
    with connect(db_file) as conn:
        yield PartStore(conn)


def _part(store: PartStore, **overrides):
    values = {"description": "Dual N-channel MOSFET", "category": "mosfet", "mpn": "IRF7910"}
    values.update(overrides)
    return store.create_part(**values)


def _sum_of_log(store: PartStore, part_id: int) -> int:
    return sum(m.delta for m in store.list_movements(part_id))


# --- restock / consume / correct -------------------------------------------


def test_restock_increases_quantity(store) -> None:
    part = _part(store, quantity_on_hand=2)

    updated = store.restock(part.id, 10)

    assert updated.quantity_on_hand == 12


def test_restock_writes_a_movement(store) -> None:
    part = _part(store)

    store.restock(part.id, 5)
    movements = store.list_movements(part.id)

    assert len(movements) == 1
    assert movements[0].delta == 5
    assert movements[0].reason is MovementReason.RESTOCK


def test_consume_decreases_quantity(store) -> None:
    part = _part(store, quantity_on_hand=10)

    updated = store.consume(part.id, 3)

    assert updated.quantity_on_hand == 7


def test_consume_writes_a_negative_movement(store) -> None:
    part = _part(store, quantity_on_hand=10)

    store.consume(part.id, 3)
    consumption = store.list_movements(part.id)[-1]

    assert consumption.delta == -3
    assert consumption.reason is MovementReason.CONSUME


def test_consume_records_the_ticket_ref(store) -> None:
    part = _part(store, quantity_on_hand=10)

    store.consume(part.id, 2, ticket_ref="26-0001")

    assert store.list_movements(part.id)[-1].ticket_ref == "26-0001"


def test_ticket_ref_is_never_validated(store) -> None:
    """Stored verbatim. bench-parts never calls intake to check a ref exists —
    loose coupling across services is the point, exactly as benchhud stores
    external_ref."""
    part = _part(store, quantity_on_hand=10)

    store.consume(part.id, 1, ticket_ref="not-a-real-ref")

    assert store.list_movements(part.id)[-1].ticket_ref == "not-a-real-ref"


def test_correct_can_go_either_way(store) -> None:
    part = _part(store, quantity_on_hand=10)

    store.correct(part.id, -2)
    store.correct(part.id, 5)

    assert store.get_part(part.id).quantity_on_hand == 13


def test_correction_is_a_new_row_not_an_edit(store) -> None:
    """History is append-only: a miscount is corrected by an opposing delta,
    never by rewriting the movement that was wrong."""
    part = _part(store, quantity_on_hand=10)

    store.consume(part.id, 3)
    store.correct(part.id, 3)

    assert len(store.list_movements(part.id)) == 3  # initial + consume + correction


# --- validation -------------------------------------------------------------


def test_cannot_consume_more_than_on_hand(store) -> None:
    part = _part(store, quantity_on_hand=2)

    with pytest.raises(ValueError, match="2"):
        store.consume(part.id, 3)


def test_failed_consume_leaves_no_movement_and_no_change(store) -> None:
    """Validation before mutation — a rejected movement leaves nothing behind."""
    part = _part(store, quantity_on_hand=2)

    with pytest.raises(ValueError):
        store.consume(part.id, 3)

    assert store.get_part(part.id).quantity_on_hand == 2
    assert len(store.list_movements(part.id)) == 1  # just the initial


def test_consuming_exactly_the_stock_is_allowed(store) -> None:
    """The boundary — taking the last one is normal, not an error."""
    part = _part(store, quantity_on_hand=2)

    updated = store.consume(part.id, 2)

    assert updated.quantity_on_hand == 0


def test_correction_cannot_drive_stock_negative(store) -> None:
    part = _part(store, quantity_on_hand=1)

    with pytest.raises(ValueError):
        store.correct(part.id, -5)


@pytest.mark.parametrize("amount", [0, -1])
def test_restock_requires_a_positive_amount(store, amount) -> None:
    part = _part(store)

    with pytest.raises(ValueError):
        store.restock(part.id, amount)


@pytest.mark.parametrize("amount", [0, -1])
def test_consume_requires_a_positive_amount(store, amount) -> None:
    """`consume` takes a positive count and negates it — passing a negative
    would otherwise silently add stock."""
    part = _part(store, quantity_on_hand=5)

    with pytest.raises(ValueError):
        store.consume(part.id, amount)


def test_zero_correction_is_rejected(store) -> None:
    """A movement that moves nothing is a bug, not a record."""
    part = _part(store, quantity_on_hand=5)

    with pytest.raises(ValueError):
        store.correct(part.id, 0)


def test_movement_against_a_missing_part_raises(store) -> None:
    with pytest.raises(LookupError):
        store.restock(9999, 1)


def test_reason_is_coerced_from_a_string(store) -> None:
    part = _part(store, quantity_on_hand=5)

    store.record_movement(part.id, -1, "consume")

    assert store.list_movements(part.id)[-1].reason is MovementReason.CONSUME


def test_unknown_reason_is_rejected(store) -> None:
    part = _part(store, quantity_on_hand=5)

    with pytest.raises(ValueError, match="shrinkage"):
        store.record_movement(part.id, -1, "shrinkage")


# --- cost snapshotting ------------------------------------------------------


def test_consumption_snapshots_the_unit_cost(store) -> None:
    part = _part(store, quantity_on_hand=10, unit_cost=187)

    store.consume(part.id, 2, ticket_ref="26-0001")

    assert store.list_movements(part.id)[-1].unit_cost_at_time == 187


def test_later_price_change_does_not_rewrite_history(store) -> None:
    """The whole reason cost is snapshotted rather than joined at read time.

    A job costed last month must not silently re-cost itself when a supplier
    raises their price.
    """
    part = _part(store, quantity_on_hand=10, unit_cost=187)
    store.consume(part.id, 2, ticket_ref="26-0001")

    store.update_part(part.id, unit_cost=999)

    assert store.list_movements(part.id)[-1].unit_cost_at_time == 187


def test_snapshot_is_none_when_the_part_has_no_cost(store) -> None:
    """Plenty of salvaged parts have no meaningful price."""
    part = _part(store, quantity_on_hand=10, unit_cost=None)

    store.consume(part.id, 1)

    assert store.list_movements(part.id)[-1].unit_cost_at_time is None


def test_consumption_cost_totals_are_reconstructable(store) -> None:
    """What Phase 4 will lean on to answer "what did the parts on 26-0001 cost?"."""
    part = _part(store, quantity_on_hand=10, unit_cost=100)
    store.consume(part.id, 2, ticket_ref="26-0001")
    store.update_part(part.id, unit_cost=250)
    store.consume(part.id, 1, ticket_ref="26-0001")

    spend = sum(
        -m.delta * m.unit_cost_at_time
        for m in store.list_movements(part.id)
        if m.ticket_ref == "26-0001"
    )

    assert spend == 2 * 100 + 1 * 250


# --- the invariant: log and cache stay together -----------------------------


def test_cache_matches_the_log_after_a_sequence(store) -> None:
    part = _part(store, quantity_on_hand=10)
    store.restock(part.id, 5)
    store.consume(part.id, 3, ticket_ref="26-0001")
    store.correct(part.id, -1)

    assert store.get_part(part.id).quantity_on_hand == _sum_of_log(store, part.id) == 11


def test_movement_and_cache_update_are_atomic(store, monkeypatch) -> None:
    """Force a failure *between* the two writes and assert both roll back.

    This is the single most important test in the phase. If the movement can
    land without its cache update (or vice versa), the log and the count
    disagree and every downstream number is quietly wrong.
    """
    part = _part(store, quantity_on_hand=10)
    real_execute = store._conn.execute
    calls = {"n": 0}

    def exploding_execute(sql, *args, **kwargs):
        # Let the movement insert through, then blow up on the cache UPDATE.
        if sql.strip().upper().startswith("UPDATE PARTS"):
            calls["n"] += 1
            raise sqlite3.OperationalError("simulated failure between writes")
        return real_execute(sql, *args, **kwargs)

    monkeypatch.setattr(store._conn, "execute", exploding_execute)

    with pytest.raises(sqlite3.OperationalError):
        store.consume(part.id, 3)

    monkeypatch.undo()

    assert calls["n"] == 1, "the cache UPDATE was never attempted — test is not exercising it"
    assert store.get_part(part.id).quantity_on_hand == 10
    assert len(store.list_movements(part.id)) == 1  # the initial only; consume rolled back
    assert store.get_part(part.id).quantity_on_hand == _sum_of_log(store, part.id)


def test_concurrent_consumption_never_drifts(db_file) -> None:
    """N threads each taking 1 from a part with N on hand.

    Measured, not assumed. A read-then-write gap would show up here as a final
    count above zero (lost updates) or as fewer movement rows than threads.
    """
    threads = 8
    with connect(db_file) as conn:
        part = PartStore(conn).create_part(
            description="Dual N-channel MOSFET", category="mosfet", quantity_on_hand=threads
        )
        part_id = part.id

    barrier = threading.Barrier(threads)
    errors: list[Exception] = []

    def take() -> None:
        try:
            barrier.wait()
            with connect(db_file) as conn:
                conn.execute("PRAGMA busy_timeout = 5000")
                PartStore(conn).consume(part_id, 1)
        except Exception as exc:  # noqa: BLE001 - collected and asserted on below
            errors.append(exc)

    workers = [threading.Thread(target=take) for _ in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    with connect(db_file) as conn:
        store = PartStore(conn)
        final = store.get_part(part_id)
        movements = store.list_movements(part_id)

    assert errors == []
    assert final.quantity_on_hand == 0
    assert len(movements) == threads + 1  # initial + one consume per thread
    assert final.quantity_on_hand == _sum_of_log(store, part_id)
