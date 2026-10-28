"""A small canary gate; stdlib only, kubectl provides Kubernetes authentication."""
import argparse
import json
import math
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen


def query(prometheus, expression, at):
    url = prometheus.rstrip("/") + "/api/v1/query?" + urlencode({"query": expression, "time": at})
    with urlopen(url, timeout=10) as response:
        payload = json.load(response)
    if payload.get("status") != "success" or payload.get("warnings"):
        raise ValueError("Prometheus query failed or returned warnings")
    result = payload["data"]["result"]
    if len(result) != 1:
        raise ValueError("Expected exactly one Prometheus sample")
    value = float(result[0]["value"][1])
    if not math.isfinite(value):
        raise ValueError("Prometheus returned a non-finite value")
    return value


def snapshot(args, at):
    selector = f'namespace="{args.namespace}",lab_release="{args.release}",lab_component="api",lab_version="v2"'
    sample = lambda expr: query(args.prometheus, expr, at)
    if sample(f'min(up{{{selector}}})') != 1:
        raise ValueError("At least one v2 scrape target is down")
    if sample(f'time() - min(timestamp(up{{{selector}}}))') > 30:
        raise ValueError("Stale scrape data")
    count = sample(f'sum(increase(shop_order_requests_total{{{selector}}}[60s]))')
    if count < args.min_requests:
        raise ValueError(f"Too few v2 attempts: {count:.1f}")
    errors = sample(f'sum(increase(shop_order_requests_total{{{selector},status=~"5.."}}[60s]))')
    p95 = sample(f'histogram_quantile(0.95, sum by (le) (rate(shop_order_duration_seconds_bucket{{{selector}}}[60s])))')
    return {"requests": count, "error_ratio": errors / count, "p95_seconds": p95}


def kubectl(args, *command):
    result = subprocess.run(["kubectl", "-n", args.namespace, *command],
                            check=True, text=True, capture_output=True, timeout=20)
    return json.loads(result.stdout)


def route_state(args):
    route = kubectl(args, "get", "httproute", args.release + "-api", "-o", "json")
    refs = route["spec"]["rules"][0]["backendRefs"]
    if len(refs) != 2 or [r["name"] for r in refs] != [args.release + "-api-v1", args.release + "-api-v2"]:
        raise ValueError("Unexpected backendRefs; refusing to change a different route")
    weights = [int(r.get("weight", 1)) for r in refs]
    if min(weights) < 0 or sum(weights) != 100:
        raise ValueError("Expected normalized weights summing to 100")
    return route, weights[1]


def accepted(route):
    generation = route["metadata"]["generation"]
    for parent in route.get("status", {}).get("parents", []):
        if parent.get("controllerName") != "linkerd.io/policy-controller":
            continue
        conditions = {c["type"]: c for c in parent.get("conditions", [])}
        # Linkerd edge-26.6.3 omits observedGeneration. Backend references stay
        # fixed; after weight changes run() still waits a full metrics window.
        # Controllers that report a generation must confirm the current one.
        if all(conditions.get(k, {}).get("status") == "True" and
               conditions[k].get("observedGeneration", generation) == generation
               for k in ("Accepted", "ResolvedRefs")):
            return True
    return False


def set_weight(args, route, weight):
    patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": route["metadata"]["resourceVersion"]},
             {"op": "replace", "path": "/spec/rules/0/backendRefs/0/weight", "value": 100 - weight},
             {"op": "replace", "path": "/spec/rules/0/backendRefs/1/weight", "value": weight}]
    kubectl(args, "patch", "httproute", args.release + "-api", "--type=json", "-p", json.dumps(patch), "-o", "json")


def decision(sample, weight, args):
    if sample["error_ratio"] >= args.max_errors or sample["p95_seconds"] >= args.max_p95:
        return 0, "rollback"
    return min(100, weight + args.step), "promote"


def emit(event, **fields):
    print(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **fields}), flush=True)


def run(args):
    ready_after = time.monotonic() + args.warmup
    deadline = time.monotonic() + args.max_duration
    good = 0
    expected_weight = None
    while time.monotonic() < deadline:
        try:
            route, weight = route_state(args)
            if expected_weight is not None and weight != expected_weight:
                raise RuntimeError("Route changed outside this gate; stopping")
            expected_weight = weight
            if weight == 0:
                emit("stopped", weight=0)
                return 2
            if not accepted(route):
                raise ValueError("HTTPRoute is not accepted at its current generation")
            if time.monotonic() < ready_after:
                emit("warmup", weight=weight)
            else:
                sample = snapshot(args, time.time())
                new_weight, action = decision(sample, weight, args)
                good = good + 1 if action == "promote" else 0
                emit("sample", weight=weight, **sample)
                if action == "rollback" or good >= args.healthy_checks:
                    if weight == 100 and action == "promote":
                        emit("complete", weight=100, **sample)
                        return 0
                    set_weight(args, route, new_weight)
                    expected_weight = new_weight
                    emit(action, weight=new_weight, previous_weight=weight, **sample)
                    if action == "rollback":
                        return 2
                    # A new full window at each weight, including a final soak at 100%.
                    good = 0
                    ready_after = time.monotonic() + args.warmup
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            good = 0
            emit("hold", reason=str(error))
        time.sleep(args.interval)
    emit("timeout", reason="No terminal decision; route left at its last confirmed weight")
    return 3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prometheus", default="http://localhost:9090")
    parser.add_argument("--namespace", default="lab6")
    parser.add_argument("--release", default="shop")
    parser.add_argument("--interval", type=float, default=15)
    parser.add_argument("--warmup", type=float, default=75)
    parser.add_argument("--max-duration", type=float, default=1800)
    parser.add_argument("--min-requests", type=int, default=30)
    parser.add_argument("--max-errors", type=float, default=.05)
    parser.add_argument("--max-p95", type=float, default=.5)
    parser.add_argument("--step", type=int, default=15)
    parser.add_argument("--healthy-checks", type=int, default=2)
    args = parser.parse_args()
    if not (args.interval > 0 and args.warmup >= 75 and args.max_duration > args.warmup
            and args.min_requests > 0 and 0 < args.max_errors <= 1 and args.max_p95 > 0
            and 1 <= args.step <= 100 and args.healthy_checks >= 1):
        parser.error("Invalid gate thresholds or timings (warmup must be >=75 seconds)")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
