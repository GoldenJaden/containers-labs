"""Optional integration test. TEST_DATABASE_URL must point to a disposable DB."""
import os
import uuid

import pytest

from api import app as api


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL not available")
def test_real_postgres_idempotency_and_payload_conflict(monkeypatch):
    monkeypatch.setattr(api,"DATABASE_URL",os.environ["TEST_DATABASE_URL"])
    api.initialize_database()
    key = str(uuid.uuid4())
    first = api.save_order(api.OrderCreate(item="coffee"),key)
    second = api.save_order(api.OrderCreate(item="coffee"),key)
    assert first["id"] == second["id"]
    with pytest.raises(api.HTTPException) as error:
        api.save_order(api.OrderCreate(item="tea"),key)
    assert error.value.status_code == 409
