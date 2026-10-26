import logging
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import psycopg
from psycopg.rows import dict_row


DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/orders"
)
POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "2"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("order-worker")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        return


def start_health_server() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8081), HealthHandler)
    Thread(target=server.serve_forever, daemon=True).start()


def process_batch() -> int:
    """Claim and mark one batch in a single transaction."""
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        orders = connection.execute(
            """
            SELECT id, item, quantity
            FROM orders
            WHERE processed = FALSE
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT %s
            """,
            (BATCH_SIZE,),
        ).fetchall()

        if not orders:
            return 0

        order_ids = [order["id"] for order in orders]
        connection.execute(
            """
            UPDATE orders
            SET processed = TRUE, processed_at = NOW()
            WHERE id = ANY(%s)
            """,
            (order_ids,),
        )

        for order in orders:
            logger.info(
                "processed order id=%s item=%r quantity=%s",
                order["id"],
                order["item"],
                order["quantity"],
            )
        return len(orders)


def run() -> None:
    start_health_server()
    logger.info("worker started")
    while True:
        try:
            processed = process_batch()
            if processed == 0:
                time.sleep(POLL_INTERVAL_SECONDS)
        except psycopg.Error:
            logger.exception("database operation failed; retrying")
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
