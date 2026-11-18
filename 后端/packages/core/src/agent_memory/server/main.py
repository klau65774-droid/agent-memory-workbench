"""Run with python -m uvicorn agent_memory.server.main:app --port 8000."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..core.context import DisclosureLevel
from ..core.errors import LockTimeoutError, NotFoundError, ValidationError
from ..core.redaction import redact
from ..core.store import Store
from . import MILLISECONDS_PER_SECOND, service
from .benchmark import run_benchmark
from .demo import NAMESPACES, Namespace, locked_store, open_namespaces
from .models import (
    BenchmarkResponse,
    HealthResponse,
    RecallRequest,
    RecallResponse,
    RecordDetail,
    RecordsResponse,
    SleepRequest,
    SleepResponse,
    WriteRequest,
    WriteResponse,
)

VERSION = "0.2.0"
_CORRELATION_HEADER = "X-Correlation-ID"
_LATENCY_HEADER = "X-Memory-Latency-Ms"
_ORIGINS = ["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:5173"]
NamespaceHeader = Annotated[Namespace, Header(alias="X-Namespace")]


class ResponseTelemetry:
    def __init__(self, application: ASGIApp):
        self.application = application

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.application(scope, receive, send)
            return
        start = time.perf_counter()
        correlation_id = str(uuid.uuid4())

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers[_CORRELATION_HEADER] = correlation_id
                latency_ms = (time.perf_counter() - start) * MILLISECONDS_PER_SECOND
                headers[_LATENCY_HEADER] = f"{latency_ms:.3f}"
            await send(message)

        await self.application(scope, receive, with_headers)


class ControlPlane(FastAPI):
    def build_middleware_stack(self) -> ASGIApp:
        # Trace and apply CORS to responses from the outer error handler and to preflights.
        return ResponseTelemetry(
            CORSMiddleware(
                super().build_middleware_stack(),
                allow_origins=_ORIGINS,
                allow_methods=["GET", "POST"],
                allow_headers=["*"],
                expose_headers=[_CORRELATION_HEADER, _LATENCY_HEADER],
            )
        )


def _store(request: Request, namespace: Namespace) -> Store:
    stores: dict[Namespace, Store] = request.app.state.stores
    return stores[namespace]


def _default_data_root() -> Path:
    source = Path(__file__).resolve()
    relative = Path("packages/core/src/agent_memory/server/main.py")
    for parent in source.parents:
        if parent / relative == source:
            return parent / ".demo_stores"
    return Path.cwd() / ".demo_stores"


def create_app(data_root: Path | None = None) -> FastAPI:
    configured_root = os.environ.get("AGENT_MEMORY_DEMO_ROOT")
    root = data_root or (Path(configured_root) if configured_root else _default_data_root())
    root = root.resolve()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.stores = await run_in_threadpool(open_namespaces, root)
        yield

    application = ControlPlane(
        title="Agent Memory Control Plane", version=VERSION, lifespan=lifespan
    )

    @application.exception_handler(RequestValidationError)
    async def request_error(_request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            content={
                "detail": [
                    {
                        "loc": [
                            redact(part) if isinstance(part, str) else part for part in issue["loc"]
                        ],
                        "type": issue["type"],
                        "msg": redact(issue["msg"]),
                    }
                    for issue in error.errors()
                ]
            },
        )

    @application.exception_handler(ValidationError)
    async def store_error(_request: Request, error: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            content={
                "detail": [
                    {"loc": ["body", field.field], "msg": redact(field.reason), "type": error.code}
                    for field in error.errors
                ]
            },
        )

    @application.exception_handler(NotFoundError)
    async def missing_memory(_request: Request, error: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=HTTPStatus.NOT_FOUND, content={"detail": redact(str(error))}
        )

    @application.exception_handler(LockTimeoutError)
    async def busy(_request: Request, error: LockTimeoutError) -> JSONResponse:
        return JSONResponse(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE, content={"detail": str(error)}
        )

    @application.get("/health")
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=VERSION, namespaces=list(NAMESPACES))

    @application.get("/v1/memory/records")
    def records(
        request: Request,
        namespace: NamespaceHeader = "default",
        include_invalid: bool = True,
        include_quarantined: bool = True,
    ) -> RecordsResponse:
        store = _store(request, namespace)
        with locked_store(store):
            return service.list_records(store, namespace, include_invalid, include_quarantined)

    @application.get("/v1/memory/records/{name}")
    def record_detail(
        request: Request,
        name: str,
        namespace: NamespaceHeader = "default",
        level: DisclosureLevel = "full",
    ) -> RecordDetail:
        store = _store(request, namespace)
        with locked_store(store):
            return service.read_record(store, name, level)

    @application.post("/v1/memory/record")
    def record(
        request: Request, body: WriteRequest, namespace: NamespaceHeader = "default"
    ) -> WriteResponse:
        store = _store(request, namespace)
        with locked_store(store):
            return service.write_record(store, body)

    @application.post("/v1/memory/recall")
    def recall(
        request: Request, body: RecallRequest, namespace: NamespaceHeader = "default"
    ) -> RecallResponse:
        store = _store(request, namespace)
        with locked_store(store):
            return service.recall_records(store, body)

    @application.post("/v1/memory/manage/sleep")
    def sleep(
        request: Request, body: SleepRequest | None = None, namespace: NamespaceHeader = "default"
    ) -> SleepResponse:
        store = _store(request, namespace)
        with locked_store(store):
            return service.sleep(store, namespace, dry_run=body.dry_run if body else False)

    @application.post("/v1/memory/evals/benchmark")
    def benchmark() -> BenchmarkResponse:
        return run_benchmark()

    return application


app = create_app()
