"""Login rate limiting.

The clock is injected rather than slept on — a test that waits out a real
cooling-off window is a test nobody runs. `test_one_ip_cannot_lock_out_another`
is the one that matters most in practice: a global counter would let anyone on
the LAN lock Aaron and Jill out of their own bench.
"""

import pytest

from bench_parts.ratelimit import RateLimiter


@pytest.fixture
def clock():
    """A hand-cranked clock, so windows can be tested without waiting."""

    class Clock:
        now = 1000.0

        def __call__(self) -> float:
            return self.now

        def advance(self, seconds: float) -> None:
            self.now += seconds

    return Clock()


@pytest.fixture
def limiter(clock):
    return RateLimiter(max_attempts=3, window_seconds=60, now=clock)


# --- counting ---------------------------------------------------------------


def test_a_fresh_client_is_allowed(limiter) -> None:
    assert limiter.allowed("10.0.0.1")


def test_attempts_under_the_limit_stay_allowed(limiter) -> None:
    for _ in range(2):
        limiter.record_failure("10.0.0.1")

    assert limiter.allowed("10.0.0.1")


def test_the_limit_locks_out(limiter) -> None:
    for _ in range(3):
        limiter.record_failure("10.0.0.1")

    assert not limiter.allowed("10.0.0.1")


def test_further_attempts_while_locked_stay_locked(limiter) -> None:
    for _ in range(10):
        limiter.record_failure("10.0.0.1")

    assert not limiter.allowed("10.0.0.1")


# --- the window -------------------------------------------------------------


def test_the_window_expires(limiter, clock) -> None:
    for _ in range(3):
        limiter.record_failure("10.0.0.1")
    assert not limiter.allowed("10.0.0.1")

    clock.advance(61)

    assert limiter.allowed("10.0.0.1")


def test_still_locked_just_before_the_window_ends(limiter, clock) -> None:
    for _ in range(3):
        limiter.record_failure("10.0.0.1")

    clock.advance(59)

    assert not limiter.allowed("10.0.0.1")


def test_old_attempts_age_out_individually(limiter, clock) -> None:
    """A slow trickle of wrong guesses must not accumulate forever into a
    lockout — the window is rolling, not a running total."""
    limiter.record_failure("10.0.0.1")
    clock.advance(30)
    limiter.record_failure("10.0.0.1")
    clock.advance(31)  # the first attempt is now outside the window
    limiter.record_failure("10.0.0.1")

    assert limiter.allowed("10.0.0.1")


# --- clearing ---------------------------------------------------------------


def test_success_clears_the_counter(limiter) -> None:
    for _ in range(2):
        limiter.record_failure("10.0.0.1")

    limiter.record_success("10.0.0.1")

    for _ in range(2):
        limiter.record_failure("10.0.0.1")
    assert limiter.allowed("10.0.0.1")


# --- isolation --------------------------------------------------------------


def test_one_ip_cannot_lock_out_another(limiter) -> None:
    """The failure that would actually hurt: someone on the LAN hammering the
    login and locking Aaron and Jill out of their own bench."""
    for _ in range(5):
        limiter.record_failure("10.0.0.99")

    assert not limiter.allowed("10.0.0.99")
    assert limiter.allowed("10.0.0.1")


def test_buckets_are_independent(limiter) -> None:
    limiter.record_failure("10.0.0.1")
    limiter.record_failure("10.0.0.2")
    limiter.record_failure("10.0.0.2")
    limiter.record_failure("10.0.0.2")

    assert limiter.allowed("10.0.0.1")
    assert not limiter.allowed("10.0.0.2")


# --- housekeeping -----------------------------------------------------------


def test_expired_buckets_are_dropped(limiter, clock) -> None:
    """Left unbounded, a scanner walking source IPs would grow this forever."""
    for i in range(50):
        limiter.record_failure(f"10.0.1.{i}")
    assert limiter.tracked_clients() == 50

    clock.advance(61)
    limiter.allowed("10.0.0.1")

    assert limiter.tracked_clients() <= 1


def test_missing_client_identity_is_not_a_free_pass(limiter) -> None:
    """An unidentifiable client shares one bucket rather than bypassing the
    limit — failing closed is the right direction here."""
    for _ in range(3):
        limiter.record_failure(None)

    assert not limiter.allowed(None)
