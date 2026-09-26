"""bench-parts — self-hosted parts inventory for a board-repair bench.

Tracks what's on the bench, flags what's running low, and serves stock to
benchhud and other LAN clients over a REST API. No PII lives here — parts data
is stream-safe, unlike its sibling benchhud-intake.
"""

__version__ = "0.2.0"  # x-release-please-version

__all__ = ["__version__"]
