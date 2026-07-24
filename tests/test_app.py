"""The app answers a health check.

`/healthz` is the probe Kubernetes hits for liveness and readiness. It stays
unauthenticated once the password gate lands in Phase 3 — it must answer before
a session exists.
"""

from fastapi.testclient import TestClient

from bench_parts.app import app

client = TestClient(app)


def test_healthz_returns_ok() -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
