import json
import sys

import pytest

from scripts import reset_route


def test_reset_restores_initial_split_after_gate_rollback(monkeypatch, capsys):
    values = {"mesh": {"enabled": True, "traffic": {"v1": 90, "v2": 10}},
              "api": {"versions": {"v1": {"enabled": True}, "v2": {"enabled": True}}},
              "database": {"password": "must-stay-private"}}
    route = {"metadata": {"resourceVersion": "7"}, "spec": {"rules": [{"backendRefs": [
        {"name": "shop-api-v1", "port": 8080, "weight": 100},
        {"name": "shop-api-v2", "port": 8080, "weight": 0}]}]}}
    monkeypatch.setattr(sys, "argv", ["reset_route.py"])
    monkeypatch.setattr(reset_route.subprocess, "check_output",
                        lambda cmd: json.dumps(values if cmd[0] == "helm" else route).encode())
    calls = []
    monkeypatch.setattr(reset_route.subprocess, "run", lambda cmd, **_: calls.append(cmd))
    reset_route.main()
    patch = json.loads(calls[0][-1])
    refs = patch[-1]["value"]
    assert [(r["name"], r["port"], r["weight"]) for r in refs] == [
        ("shop-api-v1", 8080, 90), ("shop-api-v2", 8080, 10)]
    assert "must-stay-private" not in capsys.readouterr().out


def test_reset_refuses_unexpected_route(monkeypatch):
    values = {"mesh": {"enabled": True}, "api": {"versions": {
        "v1": {"enabled": True}, "v2": {"enabled": False}}}}
    route = {"spec": {"rules": [{"backendRefs": [{"name": "another-service"}]}]}}
    monkeypatch.setattr(sys, "argv", ["reset_route.py"])
    monkeypatch.setattr(reset_route.subprocess, "check_output",
                        lambda cmd: json.dumps(values if cmd[0] == "helm" else route).encode())
    monkeypatch.setattr(reset_route.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("must not patch"))
    with pytest.raises(SystemExit, match="Unexpected route"):
        reset_route.main()
