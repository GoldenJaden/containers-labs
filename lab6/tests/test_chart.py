import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HELM = os.getenv("HELM") or shutil.which("helm")
pytestmark = pytest.mark.skipif(not HELM, reason="helm not installed")


def render(scenario):
    result = subprocess.run([HELM,"template","shop",str(ROOT/"helm/shop"),"-n","lab6",
                             "-f",str(ROOT/"values"/(scenario+".yaml"))],capture_output=True,text=True,check=True)
    return [d for d in yaml.safe_load_all(result.stdout) if d]


@pytest.mark.parametrize("scenario",["baseline","mesh-cost","flaky","bad","good"])
def test_scenario_wiring(scenario):
    docs = render(scenario)
    deployments = {d["metadata"]["name"]:d for d in docs if d["kind"] == "Deployment"}
    expected = {"shop-api-v1","shop-client"} | ({"shop-api-v2"} if scenario in {"bad","good"} else set())
    assert set(deployments) == expected
    # Every scenario provisions its own standard PostgreSQL resources.
    database = next(d for d in docs if d["kind"] == "StatefulSet")
    assert database["metadata"]["name"] == "shop-postgres"
    assert database["spec"]["replicas"] == 1
    assert database["spec"]["template"]["metadata"]["annotations"]["linkerd.io/inject"] == "disabled"
    assert database["spec"]["volumeClaimTemplates"][0]["metadata"]["name"] == "data"
    secret = next(d for d in docs if d["kind"] == "Secret")
    assert secret["metadata"]["name"] == "shop-postgres"
    assert secret["stringData"]["password"]
    assert all(not d["apiVersion"].startswith("postgresql.cnpg.io/") for d in docs)
    for d in deployments.values():
        pod = d["spec"]["template"]
        assert pod["metadata"]["annotations"]["linkerd.io/inject"] == ("disabled" if scenario=="baseline" else "enabled")
        assert d["spec"]["selector"]["matchLabels"].items() <= pod["metadata"]["labels"].items()
        if d["metadata"]["name"].startswith("shop-api-"):
            env = {e["name"]: e for e in pod["spec"]["containers"][0]["env"]}
            assert env["PGHOST"]["value"] == "shop-postgres"
            assert env["PGPASSWORD"]["valueFrom"]["secretKeyRef"] == {"name":"shop-postgres","key":"password"}
    routes = [d for d in docs if d["kind"]=="HTTPRoute" and d["metadata"]["name"]=="shop-api"]
    assert len(routes) == (0 if scenario=="baseline" else 1)
    services = {d["metadata"]["name"] for d in docs if d["kind"]=="Service"}
    if routes:
        refs = routes[0]["spec"]["rules"][0]["backendRefs"]
        assert sum(r["weight"] for r in refs)==100
        assert all(r["name"] in services for r in refs)
    monitors = [d for d in docs if d["kind"]=="ServiceMonitor"]
    assert len(monitors)==1
    assert "lab_version" in monitors[0]["spec"]["targetLabels"]
    assert len([d for d in docs if d["kind"]=="PrometheusRule"][0]["spec"]["groups"][0]["rules"])==3


def test_strict_mesh_has_health_exception():
    docs = render("good")
    health = next(d for d in docs if d["kind"]=="HTTPRoute" and d["metadata"]["name"]=="shop-health")
    assert health["spec"]["rules"][0]["matches"]==[{"path":{"type":"Exact","value":"/health"},"method":"GET"}]


def test_invalid_weights_fail_rendering():
    result = subprocess.run([HELM,"template","shop",str(ROOT/"helm/shop"),"--set","mesh.traffic.v2=80"],capture_output=True,text=True)
    assert result.returncode != 0
    assert "weights must sum to 100" in result.stderr
