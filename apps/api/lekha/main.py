from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from lekha.core.config import APP_NAME, get_settings
from lekha.core.db import dispose_engine, system_session
from lekha.core.errors import AppError, app_error_handler
from lekha.core.logging import configure_logging
from lekha.core.ratelimit import limiter

configure_logging()
log = structlog.get_logger("api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    s = get_settings()
    # Built-in GST rule versions are seeded idempotently so a fresh database is always usable.
    try:
        from lekha.services.rulebook import seed_rules

        async with system_session() as db:
            n = await seed_rules(db)
        if n:
            log.info("rules.seeded", count=n)
    except Exception:  # noqa: BLE001 - the API can still start; rule lookups fall back to the seed in code
        log.exception("rules.seed_failed")
    worker = None
    if s.run_worker_in_api:
        from lekha.jobs.queue import Worker

        worker = Worker(concurrency=s.worker_concurrency)
        worker.start()
        log.info("worker.started", concurrency=s.worker_concurrency)
    yield
    if worker:
        await worker.stop()
    await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=f"{APP_NAME} API", version="1.0.0", lifespan=lifespan,
                  description="GST invoice-to-ITC protection platform. Authenticate with a session cookie or an "
                              "`Authorization: Bearer lk_...` API key.")
    app.state.limiter = limiter
    app.add_middleware(CORSMiddleware, allow_origins=[s.web_origin], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        structlog.contextvars.bind_contextvars(request_id=rid)
        start = time.perf_counter()
        response = await call_next(request)
        ms = int((time.perf_counter() - start) * 1000)
        response.headers["x-request-id"] = rid
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["referrer-policy"] = "strict-origin-when-cross-origin"
        if request.url.path.startswith("/api") and not request.url.path.startswith("/api/files"):
            log.info("http", method=request.method, path=request.url.path, status=response.status_code, ms=ms)
        structlog.contextvars.clear_contextvars()
        return response

    app.add_exception_handler(AppError, app_error_handler)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_r: Request, exc: RequestValidationError):
        errs = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        msg = errs[0]["message"] if errs else "Invalid request"
        if errs and errs[0]["field"]:
            msg = f"{errs[0]['field']}: {msg}"
        return JSONResponse({"error": {"code": "validation", "message": msg, "details": {"errors": errs}}}, 422)

    @app.exception_handler(RateLimitExceeded)
    async def ratelimit_handler(_r: Request, _exc: RateLimitExceeded):
        return JSONResponse({"error": {"code": "rate_limited", "message": "Too many attempts. Try again later."}},
                            429)

    from lekha.routers import auth, documents, workspace

    app.include_router(auth.router)
    app.include_router(workspace.router)
    app.include_router(documents.router)
    _include_optional(app)

    @app.get("/health")
    async def health() -> dict:
        async with system_session() as db:
            await db.execute(text("select 1"))
        return {"ok": True, "app": APP_NAME}

    return app


def _include_optional(app: FastAPI) -> None:
    """Routers for later modules; each is a separate file so the app works while they are built."""
    import importlib

    for name in ("gst", "suppliers", "intelligence", "notices", "close", "copilot", "dashboard", "exports", "rules",
                 "bench", "demo"):
        try:
            mod = importlib.import_module(f"lekha.routers.{name}")
        except ModuleNotFoundError as e:
            if e.name == f"lekha.routers.{name}":
                continue
            raise
        app.include_router(mod.router)


app = create_app()
