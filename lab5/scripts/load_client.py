"""Executed inside the existing diagnostic client pod; every HTTP attempt is counted once."""
import concurrent.futures
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import urllib.request
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST

config = globals().get("CONFIG", {"seconds": 600, "rps": 5, "name": "manual", "warmup": 30, "metrics": True})
run_name = config["name"]
requests_metric = Counter("lab5_client_requests", "Actual unretried client HTTP attempts", ["run", "result"])
for outcome in ["success", "failed"]: requests_metric.labels(run_name, outcome).inc(0)
deletions_metric = Counter("lab5_chaos_deleted_pods", "Successful pod deletion requests", ["run", "component"])
for component in ["api", "worker"]: deletions_metric.labels(run_name, component).inc(0)
ips_metric = Gauge("lab5_chaos_unique_pod_ips", "Observed distinct pod IP addresses", ["run"])
ips_metric.labels(run_name).set(0)
active_metric = Gauge("lab5_load_active", "Whether the client is sending load", ["run"])
active_metric.labels(run_name).set(0)
seen = set()
lock = threading.Lock()
start_gate = threading.Event()

class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = generate_latest()
        self.send_response(200); self.send_header("Content-Type", CONTENT_TYPE_LATEST); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/start":
            start_gate.set(); self.send_response(204); self.end_headers(); return
        with lock:
            if data.get("event") == "delete" and data["uid"] not in seen:
                seen.add(data["uid"])
                deletions_metric.labels(run_name, data["component"]).inc()
            ips_metric.labels(run_name).set(data["unique_ips"])
        self.send_response(204); self.end_headers()
    def log_message(self, *_): pass

if config["metrics"]:
    server = ThreadingHTTPServer(("0.0.0.0", 9090), MetricsHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
if config.get("start_gate"):
    if not start_gate.wait(360): raise RuntimeError("Prometheus did not become ready before the load start deadline")
else:
    time.sleep(config["warmup"])
base = "http://shop-api.lab5.svc.cluster.local:8080"

def request(i):
    started = time.time(); code = 0; error = ""; order_id = None
    try:
        data = json.dumps({"item": run_name+"-"+str(i), "quantity": 1}).encode()
        req = urllib.request.Request(base+"/order", data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=4) as response:
            code = response.status; order_id = json.load(response)["id"]
    except Exception as exc: error = repr(exc)
    requests_metric.labels(run_name, "success" if code == 201 else "failed").inc()
    return {"time": started, "status": code, "error": error, "order_id": order_id, "duration": time.time()-started}

start = time.monotonic()
active_metric.labels(run_name).set(1)
with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
    futures = []
    for i in range(int(config["seconds"]*config["rps"])):
        delay = start+i/config["rps"]-time.monotonic()
        if delay > 0: time.sleep(delay)
        futures.append(pool.submit(request, i))
    records = [future.result() for future in futures]
active_metric.labels(run_name).set(0)
with open("/tmp/lab5-"+run_name+".jsonl", "w") as output:
    for record in records: output.write(json.dumps(record)+"\n")
time.sleep(3)
with urllib.request.urlopen(base+"/orders", timeout=15) as response: orders = json.load(response)
ids = {record["order_id"] for record in records if record["order_id"] is not None}
summary = {"total": len(records), "failed": sum(record["status"] != 201 for record in records),
           "pending_orders": [order["id"] for order in orders if order["id"] in ids and not order["processed"]],
           "start": records[0]["time"], "end": records[-1]["time"]}
with open("/tmp/lab5-"+run_name+"-summary.json", "w") as output: json.dump(summary, output)
print(json.dumps(summary), flush=True)
# Leave the final counters available for multiple Prometheus scrapes.
if config["metrics"]: time.sleep(20)
