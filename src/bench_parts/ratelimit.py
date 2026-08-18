"""Throttling for failed logins.

**This is the control that actually fits the threat model.** One shared password
is guessable given unlimited attempts; scrypt makes each *offline* guess
expensive, but the realistic attack is someone on the LAN hammering
``POST /login``, where the cost per guess is a network round trip rather than a
hash. Limiting attempts is the lever; hash choice mostly isn't.

**Honest about what this is.** In-memory, so it resets on restart. Single-replica
only — two pods would each keep their own count. Defeated by an attacker who can
change source IP. For a LAN service with one password and a Deployment pinned at
``replicas: 1``, that's proportionate; a shared Redis counter would be more
machinery than the problem deserves. If any of those assumptions change, this
needs to change with them.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

#: Wrong guesses tolerated inside the window before refusing.
DEFAULT_MAX_ATTEMPTS = 5

#: How long the window is, and how long a lockout lasts. Long enough to make
#: guessing tedious, short enough that locking yourself out at the bench is an
#: annoyance rather than an outage.
DEFAULT_WINDOW_SECONDS = 300


class RateLimiter:
    """A rolling-window failure counter, bucketed per client."""

    def __init__(
        self,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        # Injected so windows are testable without sleeping through them, and
        # monotonic by default so a clock adjustment can't extend or clear a
        # lockout.
        self._now = now
        self._failures: dict[str, deque[float]] = {}

    def allowed(self, client: str | None) -> bool:
        """Whether this client may attempt a login right now."""
        attempts = self._recent(self._key(client))
        self._evict_expired()
        return len(attempts) < self.max_attempts

    def record_failure(self, client: str | None) -> None:
        """Note a wrong password."""
        key = self._key(client)
        attempts = self._recent(key)
        attempts.append(self._now())
        self._failures[key] = attempts

    def record_success(self, client: str | None) -> None:
        """Forget this client's failures — they clearly know the password."""
        self._failures.pop(self._key(client), None)

    def retry_after(self, client: str | None) -> int:
        """Seconds until this client may try again. Zero when not locked out."""
        attempts = self._recent(self._key(client))
        if len(attempts) < self.max_attempts:
            return 0
        return max(0, round(attempts[0] + self.window_seconds - self._now()))

    def tracked_clients(self) -> int:
        """How many buckets are held. Exposed so a test can prove they're freed."""
        return len(self._failures)

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _key(client: str | None) -> str:
        """Bucket name for a client.

        An unidentifiable client shares a single bucket rather than skipping the
        limit: failing closed is the right direction when the alternative is a
        free pass for anyone who can hide their address.
        """
        return client or "<unknown>"

    def _recent(self, key: str) -> deque[float]:
        """This client's failures inside the window, oldest first.

        Rolling rather than a running total, so a slow trickle of wrong guesses
        ages out instead of accumulating into a lockout days later.
        """
        cutoff = self._now() - self.window_seconds
        attempts = self._failures.get(key, deque())
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        return attempts

    def _evict_expired(self) -> None:
        """Drop buckets with nothing left in them.

        Without this, a scanner walking source addresses would grow the dict
        without bound — a slow memory leak on a Pi with no obvious cause.
        """
        cutoff = self._now() - self.window_seconds
        for key in [k for k, v in self._failures.items() if not v or v[-1] <= cutoff]:
            del self._failures[key]
