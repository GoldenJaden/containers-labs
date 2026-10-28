"""Minimal versioned orders API for the service-mesh experiments."""
import os
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

import psycopg
from fastapi import FastAPI, Header, HTTPException, Response
from prometheus_client import Counter, Histogram, start_http_server
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

# An empty DSN uses the standard PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD.
DATABASE_URL = os.getenv("DATABASE_URL", "")
VERSION = os.getenv("VERSION", "v1")
FLAKY_RATE = float(os.getenv("FLAKY_RATE", "0.25"))
if VERSION not in {"v1", "v2"} or not 0 <= FLAKY_RATE <= 1:
    raise ValueError("VERSION must be v1/v2 and FLAKY_RATE must be in [0, 1]")

REQUESTS = Counter("shop_order_requests_total", "Backend attempts to create an order", ["version", "status"])
LATENCY = Histogram("shop_order_duration_seconds", "Backend order latency", ["version"],
                    buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2, 5))
for code in (201, 409, 500):
    REQUESTS.labels(VERSION, str(code))
LATENCY.labels(VERSION)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS orders (
    id BIGSERIAL PRIMARY KEY,
    item TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    idempotency_key TEXT NOT NULL UNIQUE
)
"""


class OrderCreate(BaseModel):
    item: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1)


class Order(BaseModel):
    id: int
    item: str
    quantity: int
    created_at: datetime


def enabled(name: str) -> bool:
    return os.getenv(name, "false").lower() in {"true", "1", "yes", "on"}


def initialize_database() -> None:
    for attempt in range(30):
        try:
            with psycopg.connect(DATABASE_URL) as connection:
                # Serializes startup DDL when v1/v2 replicas start together.
                connection.execute("SELECT pg_advisory_xact_lock(600600)")
                connection.execute(CREATE_TABLE)
            return
        except psycopg.OperationalError:
            if attempt == 29:
                raise
            time.sleep(1)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    server, thread = start_http_server(int(os.getenv("METRICS_PORT", "9090")))
    yield
    server.shutdown()
    server.server_close()
    thread.join()


app = FastAPI(title="Shop API", lifespan=lifespan)


@app.get("/health", response_class=Response)
def health():
    return Response("ok\n", media_type="text/plain")


@app.get("/version", response_class=Response)
def version():
    return Response(VERSION + "\n", media_type="text/plain")


def save_order(order: OrderCreate, key: str) -> dict:
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        row = connection.execute(
            """INSERT INTO orders (item, quantity, idempotency_key)
               VALUES (%s, %s, %s) ON CONFLICT (idempotency_key) DO NOTHING
               RETURNING *""", (order.item, order.quantity, key)
        ).fetchone()
        if row is None:
            row = connection.execute("SELECT * FROM orders WHERE idempotency_key = %s", (key,)).fetchone()
        if row["item"] != order.item or row["quantity"] != order.quantity:
            raise HTTPException(409, "Idempotency-Key already used for a different order")
        return row


@app.post("/order", response_model=Order, status_code=201)
def create_order(order: OrderCreate,
                 idempotency_key: Annotated[str, Header(min_length=1, max_length=128)]):
    started = time.perf_counter()
    code = 500
    try:
        if VERSION == "v2" and enabled("FAIL"):
            raise HTTPException(500, "forced v2 failure")
        if VERSION == "v1" and enabled("FLAKY") and random.random() < FLAKY_RATE:
            raise HTTPException(500, "random v1 failure")
        result = save_order(order, idempotency_key)
        code = 201
        return result
    except HTTPException as error:
        code = error.status_code
        raise
    finally:
        REQUESTS.labels(VERSION, str(code)).inc()
        LATENCY.labels(VERSION).observe(time.perf_counter() - started)


@app.get("/orders", response_model=list[Order])
def list_orders():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        return connection.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100").fetchall()
