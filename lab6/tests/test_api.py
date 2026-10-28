from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from api import app as api


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "VERSION", "v1")
    monkeypatch.delenv("FAIL", raising=False)
    monkeypatch.delenv("FLAKY", raising=False)
    # No lifespan here: these checks intentionally do not claim a DB connection.
    return TestClient(api.app, raise_server_exceptions=False)


def test_version_and_health(client):
    assert client.get("/version").text.strip() == "v1"
    assert client.get("/health").status_code == 200


def test_post_requires_idempotency_key(client):
    assert client.post("/order", json={"item": "coffee"}).status_code == 422


def test_bad_v2_rejects_before_database(client, monkeypatch):
    monkeypatch.setattr(api, "VERSION", "v2")
    monkeypatch.setenv("FAIL", "true")
    db = Mock(side_effect=AssertionError("failure must happen before a write"))
    monkeypatch.setattr(api, "save_order", db)
    before = api.REQUESTS.labels("v2", "500")._value.get()
    r = client.post("/order", json={"item": "coffee"}, headers={"Idempotency-Key": "one"})
    assert r.status_code == 500
    assert api.REQUESTS.labels("v2", "500")._value.get() == before + 1
    db.assert_not_called()
    assert client.get("/health").status_code == 200


def test_flaky_backend_can_fail_and_succeed(client, monkeypatch):
    monkeypatch.setenv("FLAKY", "true")
    outcomes = iter([.1, .8])
    monkeypatch.setattr(api.random, "random", lambda: next(outcomes))
    order = {"id": 1, "item": "coffee", "quantity": 1,
             "created_at": datetime.now(timezone.utc)}
    db = Mock(return_value=order)
    monkeypatch.setattr(api, "save_order", db)
    responses = [client.post("/order", json={"item": "coffee"},
                            headers={"Idempotency-Key": "one"}) for _ in range(2)]
    assert [r.status_code for r in responses] == [500, 201]
    db.assert_called_once()


def test_fail_switch_does_not_break_v1(client, monkeypatch):
    monkeypatch.setenv("FAIL", "true")
    monkeypatch.setattr(api, "save_order", Mock(side_effect=api.HTTPException(409, "conflict")))
    assert client.post("/order", json={"item": "coffee"},
                       headers={"Idempotency-Key": "one"}).status_code == 409


def test_invalid_quantity_is_rejected(client):
    assert client.post("/order", json={"item": "coffee", "quantity": 0},
                       headers={"Idempotency-Key": "one"}).status_code == 422
