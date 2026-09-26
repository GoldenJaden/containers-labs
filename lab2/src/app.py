import asyncio
import json
import logging
import os
import random
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for field in ("method", "route", "status", "duration_ms", "trace_id", "span_id"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, separators=(",", ":"))


def configure_logger() -> logging.Logger:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("api")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def configure_tracing() -> TracerProvider:
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: "api"}))
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return provider


registry = CollectorRegistry()
requests_total = Counter(
    "api_http_requests_total",
    "Total number of HTTP requests.",
    ("method", "route", "status"),
    registry=registry,
)
errors_total = Counter(
    "api_http_errors_total",
    "Total number of HTTP responses with a 5xx status.",
    ("method", "route", "status"),
    registry=registry,
)
request_duration = Histogram(
    "api_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route", "status"),
    registry=registry,
)

logger = configure_logger()
tracer_provider = configure_tracing()
tracer = trace.get_tracer("lab2-api")
HTTPXClientInstrumentor().instrument()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    tracer_provider.shutdown()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def observe(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - started
    route_object = request.scope.get("route")
    route = getattr(route_object, "path", "unmatched")
    status = str(response.status_code)

    requests_total.labels(request.method, route, status).inc()
    request_duration.labels(request.method, route, status).observe(duration)
    if response.status_code >= 500:
        errors_total.labels(request.method, route, status).inc()

    span_context = trace.get_current_span().get_span_context()
    logger.info(
        "request completed",
        extra={
            "method": request.method,
            "route": route,
            "status": response.status_code,
            "duration_ms": round(duration * 1000, 3),
            "trace_id": trace.format_trace_id(span_context.trace_id),
            "span_id": trace.format_span_id(span_context.span_id),
        },
    )
    return response


@app.get("/health")
async def health() -> Response:
    return Response("ok\n", media_type="text/plain")


@app.get("/fail")
async def fail() -> None:
    span = trace.get_current_span()
    error = RuntimeError("intentional failure")
    span.record_exception(error)
    span.set_status(Status(StatusCode.ERROR, str(error)))
    raise HTTPException(status_code=500, detail=str(error))


@app.get("/slow")
async def slow() -> dict[str, int | str]:
    with tracer.start_as_current_span("slow-op") as span:
        delay_ms = random.randint(1000, 3000)
        span.set_attribute("sleep.duration_ms", delay_ms)
        await asyncio.sleep(delay_ms / 1000)
    return {"status": "ok", "delay_ms": delay_ms}


@app.get("/load")
async def load(n: int = Query(100, ge=1, le=1000)) -> dict[str, int]:
    self_url = os.getenv("SELF_URL", "http://127.0.0.1:8080")
    async with httpx.AsyncClient(timeout=10) as client:
        results = await asyncio.gather(
            *(client.get(f"{self_url}/health") for _ in range(n)),
            return_exceptions=True,
        )
    succeeded = sum(
        isinstance(result, httpx.Response) and result.status_code < 400
        for result in results
    )
    return {"requested": n, "succeeded": succeeded, "failed": n - succeeded}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(registry), media_type="text/plain; version=0.0.4")


FastAPIInstrumentor.instrument_app(app)
