import json
import logging
from io import StringIO

import httpx
from fastapi.testclient import TestClient

from app import JsonFormatter, app, logger


def test_health_fail_metrics_and_json_logs():
    log_output = StringIO()
    handler = logging.StreamHandler(log_output)
    handler.setFormatter(JsonFormatter())
    original_handlers = logger.handlers
    logger.handlers = [handler]
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health").text == "ok\n"
        assert client.get("/fail").status_code == 500
        metrics = client.get("/metrics").text
    logger.handlers = original_handlers

    assert 'api_http_errors_total{method="GET",route="/fail",status="500"} 1.0' in metrics
    log_lines = log_output.getvalue().splitlines()
    assert log_lines
    entries = [json.loads(line) for line in log_lines]
    assert all(len(entry["trace_id"]) == 32 for entry in entries)


def test_load(monkeypatch):
    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, _url):
            return httpx.Response(200)

    monkeypatch.setattr("app.httpx.AsyncClient", FakeClient)
    with TestClient(app) as client:
        response = client.get("/load?n=10")

    assert response.status_code == 200
    assert response.json() == {"requested": 10, "succeeded": 10, "failed": 0}
