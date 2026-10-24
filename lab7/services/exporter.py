import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from common import json_response, read_json


lock = threading.Lock()
state = {
    "runs_success": 0,
    "runs_error": 0,
    "agent_duration_seconds": 0.0,
    "drift_high": 0,
    "drift_medium": 0,
    "drift_low": 0,
    "resources_out_of_sync": 0,
    "last_success_timestamp_seconds": 0.0,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)

    def do_GET(self):
        if self.path == "/health":
            json_response(self, 200, {"status": "ok", "service": "drift-exporter"})
            return
        if self.path != "/metrics":
            json_response(self, 404, {"error": "not found"})
            return
        with lock:
            body = """# HELP drift_workflow_runs_total Completed drift workflow runs.
# TYPE drift_workflow_runs_total counter
drift_workflow_runs_total{{status="success"}} {runs_success}
drift_workflow_runs_total{{status="error"}} {runs_error}
# HELP drift_agent_duration_seconds Duration of the latest AI agent run.
# TYPE drift_agent_duration_seconds gauge
drift_agent_duration_seconds {agent_duration_seconds}
# HELP drift_items Latest number of drift items by severity.
# TYPE drift_items gauge
drift_items{{severity="high"}} {drift_high}
drift_items{{severity="medium"}} {drift_medium}
drift_items{{severity="low"}} {drift_low}
# HELP drift_resources_out_of_sync Latest number of resources with drift.
# TYPE drift_resources_out_of_sync gauge
drift_resources_out_of_sync {resources_out_of_sync}
# HELP drift_last_success_timestamp_seconds Unix timestamp of the latest successful audit.
# TYPE drift_last_success_timestamp_seconds gauge
drift_last_success_timestamp_seconds {last_success_timestamp_seconds}
""".format(**state).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/observe":
            json_response(self, 404, {"error": "not found"})
            return
        payload = read_json(self)
        metrics = payload.get("metrics", {})
        with lock:
            if metrics.get("status") == "success":
                state["runs_success"] += 1
                state["last_success_timestamp_seconds"] = time.time()
            else:
                state["runs_error"] += 1
            state["agent_duration_seconds"] = float(metrics.get("agent_duration_seconds", 0))
            by_severity = metrics.get("drift_by_severity", {})
            state["drift_high"] = int(by_severity.get("high", 0))
            state["drift_medium"] = int(by_severity.get("medium", 0))
            state["drift_low"] = int(by_severity.get("low", 0))
            state["resources_out_of_sync"] = int(metrics.get("resources_out_of_sync", 0))
        json_response(self, 200, {"observed": True, **payload})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"Listening on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
