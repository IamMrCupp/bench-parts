"""Shared fixtures for the web-layer tests.

The app resolves its database path from ``BENCH_PARTS_DB_PATH`` on every
request, so pointing that at a per-test temp file gives each test a clean,
isolated database — and the same path can be opened directly for seeding or
asserting, without going through HTTP.

Store-layer tests build their own connection and don't use these.
"""

import pytest
from fastapi.testclient import TestClient

from bench_parts.db import connect
from bench_parts.schema import initialise
from bench_parts.store import PartStore


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """A fresh database file, wired into the app via the env var."""
    monkeypatch.delenv("BENCH_PARTS_SQLITE_NFS", raising=False)
    path = tmp_path / "parts.db"
    monkeypatch.setenv("BENCH_PARTS_DB_PATH", str(path))
    with connect(path) as conn:
        initialise(conn)
    return path


@pytest.fixture
def store(db_path):
    """Direct store access to the same database the app is serving.

    For seeding a scenario and for asserting on what a request actually wrote —
    the test shouldn't have to scrape HTML to learn that a movement landed.
    """
    with connect(db_path) as conn:
        yield PartStore(conn)


@pytest.fixture
def client(db_path):
    """A test client against the app, database already wired.

    Imported inside the fixture so the env var is set before the app module is
    first touched — the app itself reads the path per request, but keeping the
    order explicit costs nothing and avoids a confusing failure later.
    """
    from bench_parts.app import app

    return TestClient(app)
