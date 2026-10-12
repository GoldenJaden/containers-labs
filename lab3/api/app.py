import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Response, status
from pydantic import BaseModel, Field
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


@app.get("/health", response_class=Response)
def health() -> Response:
    if os.getenv("HEALTH_FAIL", "false").lower() in {"1", "true", "yes", "on"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="health check forced to fail",
        )
    return Response(content="ok\n", media_type="text/plain")


@app.get("/metrics", response_class=Response)
def metrics() -> Response:
    health_status = (
        0
        if os.getenv("HEALTH_FAIL", "false").lower() in {"1", "true", "yes", "on"}
        else 1
    )
    body = (
        "# HELP lab3_api_health_status Whether the API health check is passing.\n"
        "# TYPE lab3_api_health_status gauge\n"
        f"lab3_api_health_status {health_status}\n"
    )
    return Response(content=body, media_type="text/plain; version=0.0.4")


@app.post("/order", response_model=Order, status_code=status.HTTP_201_CREATED)
def create_order(order: OrderCreate, connection: Connection) -> dict:
    return connection.execute(
        """
        INSERT INTO orders (item, quantity)
        VALUES (%s, %s)
        RETURNING id, item, quantity, processed, created_at, processed_at
        """,
        (order.item, order.quantity),
    ).fetchone()


@app.get("/orders", response_model=list[Order])
def list_orders(connection: Connection) -> list[dict]:
    return connection.execute(
        """
        SELECT id, item, quantity, processed, created_at, processed_at
        FROM orders
        ORDER BY id
        """
    ).fetchall()
