"""Fixed arrival-rate load; no HTTP retries in the client application."""
import argparse
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from prometheus_client import Counter, Histogram, start_http_server

REQUESTS = Counter("shop_client_requests_total", "Completed logical requests", ["status"])
LATENCY = Histogram("shop_client_duration_seconds", "End-to-end order latency",
                    buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2, 5))
DROPPED = Counter("shop_client_dropped_total", "Requests not started because workers were busy")
for code in (201, 500, 0):
    REQUESTS.labels(str(code))


def send(url):
    started = time.perf_counter()
    status = 0
    try:
        request = Request(url.rstrip("/") + "/order", method="POST",
                          data=b'{"item":"coffee","quantity":1}',
                          headers={"Content-Type": "application/json", "Idempotency-Key": str(uuid.uuid4())})
        with urlopen(request, timeout=5) as response:
            response.read()
            status = response.status
    except HTTPError as error:
        error.read()
        status = error.code
    except (URLError, TimeoutError, OSError):
        pass
    finally:
        REQUESTS.labels(str(status)).inc()
        LATENCY.observe(time.perf_counter() - started)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--rps", type=float, default=10)
    parser.add_argument("--seconds", type=float, default=0)
    args = parser.parse_args()
    if args.rps <= 0 or args.seconds < 0:
        parser.error("rps must be positive; seconds must be nonnegative")
    start_http_server(9090)
    slots = threading.BoundedSemaphore(32)
    started = time.monotonic()
    target = started
    print(json.dumps({"event": "load_started", "url": args.url, "rps": args.rps}), flush=True)
    with ThreadPoolExecutor(max_workers=32) as executor:
        while args.seconds == 0 or time.monotonic() - started < args.seconds:
            if slots.acquire(blocking=False):
                executor.submit(send, args.url).add_done_callback(lambda _: slots.release())
            else:
                DROPPED.inc()
            target += 1 / args.rps
            time.sleep(max(0, target - time.monotonic()))


if __name__ == "__main__":
    main()
