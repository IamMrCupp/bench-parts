"""Migrations run at startup — a bad one fails the pod, not the first click."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from bench_parts import app as app_module
from bench_parts.schema import SCHEMA_VERSION


def test_startup_initialises_the_database_before_any_request(db_path) -> None:
    assert not db_path.exists()

    with TestClient(app_module.app):
        pass  # lifespan ran; no request made

    assert db_path.exists()
    assert sqlite3.connect(db_path).execute("pragma user_version").fetchone()[0] == SCHEMA_VERSION


def test_startup_fails_loudly_when_the_migration_fails(db_path, monkeypatch) -> None:
    def boom(conn):
        raise sqlite3.OperationalError("simulated migration failure")

    monkeypatch.setattr(app_module, "initialise", boom)

    with pytest.raises(sqlite3.OperationalError), TestClient(app_module.app):
        pass
