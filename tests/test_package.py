"""The package imports and reports a version.

Thin on purpose — it exists so `pytest` has something to collect before there's
real code, and so a broken `src/` layout fails the suite rather than passing an
empty run.
"""

import parts


def test_version_is_exposed() -> None:
    assert parts.__version__
