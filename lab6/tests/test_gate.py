import json
from types import SimpleNamespace

import pytest

from gate import gate


def options(**overrides):
    return SimpleNamespace(**(dict(namespace="lab6", release="shop", prometheus="http://prom",
                                  min_requests=30, max_errors=.05, max_p95=.5, step=15,
                                  warmup=75, interval=15, max_duration=1200, healthy_checks=2) | overrides))


@pytest.mark.parametrize("errors,p95,expected", [(0,.1,25),(.05,.1,0),(.01,.5,0),(1,.01,0)])
def test_decision_thresholds(errors,p95,expected):
    assert gate.decision({"error_ratio": errors, "p95_seconds": p95},10,options())[0] == expected


@pytest.mark.parametrize("samples", [[0], [1,31], [1,0,29], [1,0,100,0,float("nan")]])
def test_missing_or_bad_metrics_hold(samples, monkeypatch):
    iterator = iter(samples)
    def mock_query(*_):
        value = next(iterator)
        if value != value:
            raise ValueError("non-finite")
        return value
    monkeypatch.setattr(gate,"query",mock_query)
    with pytest.raises(ValueError):
        gate.snapshot(options(),1000)


def test_gate_uses_backend_not_client_success(monkeypatch):
    queries = []
    values = iter([1,0,100,100,.01])
    def mock_query(_, expr, at):
        queries.append(expr)
        return next(values)
    monkeypatch.setattr(gate,"query",mock_query)
    sample = gate.snapshot(options(),1000)
    assert gate.decision(sample,10,options())[0] == 0
    assert any("shop_order_requests_total" in q for q in queries)
    assert all("shop_client_requests_total" not in q for q in queries)


def test_patch_protects_concurrent_changes(monkeypatch):
    calls = []
    monkeypatch.setattr(gate,"kubectl",lambda *a: calls.append(a))
    gate.set_weight(options(),{"metadata":{"resourceVersion":"42"}},0)
    patch = json.loads(calls[0][calls[0].index("-p")+1])
    assert patch[0] == {"op":"test","path":"/metadata/resourceVersion","value":"42"}
    assert [op["value"] for op in patch[1:]] == [100,0]


@pytest.mark.parametrize("bad", [False, True])
def test_complete_release_loop(bad,monkeypatch):
    clock = [0]
    weight = [10]
    patches = []
    events = []
    monkeypatch.setattr(gate.time,"monotonic",lambda: clock[0])
    monkeypatch.setattr(gate.time,"time",lambda: clock[0])
    monkeypatch.setattr(gate.time,"sleep",lambda seconds: clock.__setitem__(0,clock[0]+seconds))
    monkeypatch.setattr(gate,"route_state",lambda _: ({},weight[0]))
    monkeypatch.setattr(gate,"accepted",lambda _: True)
    monkeypatch.setattr(gate,"snapshot",lambda *_: {"requests":100,"error_ratio":int(bad),"p95_seconds":.02})
    monkeypatch.setattr(gate,"emit",lambda event,**fields: events.append((event,clock[0],fields)))
    def patch(_,route,new):
        weight[0] = new
        patches.append((new,clock[0]))
    monkeypatch.setattr(gate,"set_weight",patch)
    result = gate.run(options())
    if bad:
        assert result == 2 and patches == [(0,75)]
    else:
        assert result == 0
        assert [w for w,t in patches] == [25,40,55,70,85,100]
        assert events[-1][0] == "complete"
        assert events[-1][1] - patches[-1][1] >= 75


def test_missing_telemetry_never_promotes(monkeypatch):
    clock = [0]
    monkeypatch.setattr(gate.time,"monotonic",lambda: clock[0])
    monkeypatch.setattr(gate.time,"sleep",lambda n: clock.__setitem__(0,clock[0]+n))
    monkeypatch.setattr(gate,"route_state",lambda _: ({},10))
    monkeypatch.setattr(gate,"accepted",lambda _: True)
    def missing(*_):
        raise ValueError("missing telemetry")
    monkeypatch.setattr(gate,"snapshot",missing)
    monkeypatch.setattr(gate,"set_weight",lambda *_: pytest.fail("must hold"))
    assert gate.run(options(max_duration=120)) == 3


def test_accepted_requires_current_generation():
    route = {"metadata":{"generation":2},"status":{"parents":[
        {"controllerName":"linkerd.io/policy-controller","conditions":[
            {"type":kind,"status":"True","observedGeneration":1} for kind in ["Accepted","ResolvedRefs"]]}]}}
    assert not gate.accepted(route)
    for condition in route["status"]["parents"][0]["conditions"]:
        condition["observedGeneration"] = 2
    assert gate.accepted(route)


def test_accepted_handles_linkerd_status_without_observed_generation():
    route = {"metadata": {"generation": 2}, "status": {"parents": [
        {"controllerName": "linkerd.io/policy-controller", "conditions": [
            {"type": kind, "status": "True"} for kind in ["Accepted", "ResolvedRefs"]]}]}}
    assert gate.accepted(route)
    route["status"]["parents"][0]["conditions"][1]["status"] = "False"
    assert not gate.accepted(route)
