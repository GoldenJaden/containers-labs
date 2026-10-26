import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, Info
from prometheus_client import generate_latest
from psycopg.rows import dict_row


DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/orders"
)

CREATE_ORDERS_TABLE = """
CREATE TABLE IF NOT EXISTS orders (
    id BIGSERIAL PRIMARY KEY,
    item TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    processed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ
)
"""

HTTP_REQUESTS = Counter(
    "api_http_requests_total",
    "Total number of HTTP requests handled by the API.",
    ("method", "route", "status_code"),
)
HTTP_REQUEST_DURATION = Histogram(
    "api_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
HTTP_REQUESTS_IN_FLIGHT = Gauge(
    "api_http_requests_in_flight",
    "Number of HTTP requests currently being handled by the API.",
)
API_HEALTH_STATUS = Gauge(
    "lab4_api_health_status",
    "Whether the API health check is passing.",
)
ORDERS_CREATED = Counter(
    "api_orders_created_total",
    "Total number of orders successfully created.",
)
API_BUILD = Info("api_build", "API build information.")
API_BUILD.info({"version": os.getenv("APP_VERSION", "unknown")})


class OrderCreate(BaseModel):
    item: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1)


class Order(BaseModel):
    id: int
    item: str
    quantity: int
    processed: bool
    created_at: datetime
    processed_at: datetime | None


def database_connection():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        yield connection


Connection = Annotated[psycopg.Connection, Depends(database_connection)]


def initialize_database(attempts: int = 30, delay_seconds: float = 1.0) -> None:
    for attempt in range(1, attempts + 1):
        try:
            with psycopg.connect(DATABASE_URL) as connection:
                connection.execute(CREATE_ORDERS_TABLE)
            return
        except psycopg.OperationalError:
            if attempt == attempts:
                raise
            time.sleep(delay_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="Orders API", lifespan=lifespan)


def health_status_value() -> int:
    return (
        0
        if os.getenv("HEALTH_FAIL", "false").lower() in {"1", "true", "yes", "on"}
        else 1
    )


@app.middleware("http")
async def observe_http_requests(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)

    started_at = time.perf_counter()
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    HTTP_REQUESTS_IN_FLIGHT.inc()
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        HTTP_REQUESTS.labels(
            method=request.method,
            route=route_path,
            status_code=str(status_code),
        ).inc()
        HTTP_REQUEST_DURATION.labels(
            method=request.method,
            route=route_path,
        ).observe(time.perf_counter() - started_at)
        HTTP_REQUESTS_IN_FLIGHT.dec()


@app.get("/health", response_class=Response)
def health() -> Response:
    health_status = health_status_value()
    API_HEALTH_STATUS.set(health_status)
    if not health_status:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="health check forced to fail",
        )
    return Response(content="ok\n", media_type="text/plain")


@app.get("/metrics", response_class=Response)
def metrics() -> Response:
    API_HEALTH_STATUS.set(health_status_value())
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/order", response_model=Order, status_code=status.HTTP_201_CREATED)
def create_order(order: OrderCreate, connection: Connection) -> dict:
    created_order = connection.execute(
        """
        INSERT INTO orders (item, quantity)
        VALUES (%s, %s)
        RETURNING id, item, quantity, processed, created_at, processed_at
        """,
        (order.item, order.quantity),
    ).fetchone()
    ORDERS_CREATED.inc()
    return created_order


@app.get("/orders", response_model=list[Order])
def list_orders(connection: Connection) -> list[dict]:
    return connection.execute(
        """
        SELECT id, item, quantity, processed, created_at, processed_at
        FROM orders
        ORDER BY id
        """
    ).fetchall()
