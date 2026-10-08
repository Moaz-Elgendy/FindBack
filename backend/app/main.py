import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from app.database import SessionLocal, init_db
from app.celery_app import celery
from fastapi.middleware.cors import CORSMiddleware
from app.routers import ingest, search, items, user_context, auth, collections
from app.services import metrics, observability, privacy, retention
from app.log_filters import (SecretMask, install_log_filters,
                             install_privacy_filtering,
                             install_secret_masking)
from app import env

log = logging.getLogger("findback.api")

CONTENT_TYPE_PROMETHEUS = "text/plain; version=0.0.4; charset=utf-8"


class RequestContext:
    """Give every request an id, and record how long it took.

    Phase 18. Written as raw ASGI rather than BaseHTTPMiddleware because this
    has to be the outermost thing that runs: the id has to exist before any
    route, and the duration has to include the route. BaseHTTPMiddleware also
    re-enters the app through the task queue, which adds a frame to every
    request for no benefit here.

    Three things it deliberately does NOT log: the query string (a search
    query is the user recalling something from memory, which is private), the
    body, and the raw path with its ids. The route TEMPLATE is logged instead,
    so `/api/v1/items/<uuid>` appears as `/api/v1/items/{item_id}`.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers") or []}
        incoming = headers.get(observability.REQUEST_ID_HEADER.lower())
        request_id = observability.validate_request_id(
            incoming.decode("latin-1") if incoming else None)

        token = observability.bind_request_id(request_id)
        started = time.monotonic()
        status_holder = {"status": 500}

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                status_holder["status"] = message.get("status", 500)
                raw = message.get("headers")
                if raw is None:
                    raw = []
                raw.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": raw}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = int(round((time.monotonic() - started) * 1000))
            observability.reset_request_id(token)
            observability.log_event(
                "http.request", status=str(status_holder["status"]),
                duration_ms=duration_ms, stage=_route_template(scope))


def _route_template(scope) -> str:
    """The route's pattern, or 'unmatched'. Never the raw path."""
    route = scope.get("route")
    return observability.safe_route_template(getattr(route, "path", None))


# Phase 14: both filters live in app/log_filters.py so that every process that
# logs user content installs the same ones -- the API here, and the Celery
# worker in app/celery_app.py. The previous private names are kept as aliases
# for existing callers and tests.
_install_secret_masking = install_secret_masking
_install_privacy_filtering = install_privacy_filtering


@asynccontextmanager
async def lifespan(app: FastAPI):
    install_log_filters()
    # Print the resolved AI configuration before anything else: most provider
    # problems are a placeholder key or a model id that no longer exists, and
    # both are visible here without waiting for a failing ingest.
    try:
        log.info("ai config: %s", env.summary())
        for problem in env.warnings():
            log.warning("config: %s", problem)
    except Exception as exc:  # diagnostics must never block startup
        log.warning("ai config summary failed: %s", exc)
    # Replaces the deprecated @app.on_event("startup") handler and the
    # unconditional create_all that raced `alembic upgrade head`.
    try:
        app.state.schema_bootstrap = init_db()
        log.info("schema bootstrap: %s", app.state.schema_bootstrap)
    except Exception as exc:
        log.error("schema bootstrap failed: %s", exc)
        raise
    yield



app = FastAPI(
    title="FindBack API",
    version="1.0.0",
    description="AI-powered memory for the internet — Save → Find → Use",
    lifespan=lifespan,
)

origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:8081").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=True)
# Outermost, so a request id exists before CORS or any route runs.
app.add_middleware(RequestContext)

app.include_router(ingest.router)
app.include_router(search.router)
app.include_router(items.router)
app.include_router(user_context.router)
app.include_router(auth.router)
app.include_router(collections.router)

@app.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics():
    """Metrics in Prometheus text format.

    Unauthenticated on purpose: a scraper has no user identity, and every
    metric here is a count or a duration drawn from a closed vocabulary of
    labels. No user id, content id or URL appears in the output, so there is
    nothing here that an anonymous caller could learn about a person.

    The queue metrics come from the database, because a Celery worker is a
    separate process whose in-memory counters this endpoint could never see.
    A database that is down does not fail the scrape: the queue families are
    simply left at their last known values and the rest still render.
    """
    try:
        with SessionLocal() as db:
            metrics.collect_queue_metrics(db)
    except Exception as exc:  # noqa: BLE001 - a scrape must never 500
        log.warning("queue metrics unavailable: %s",
                    observability.describe_exc(exc))
    return PlainTextResponse(metrics.render(),
                             media_type=CONTENT_TYPE_PROMETHEUS)

@app.get("/health")
def health():
    checks = {"db": "down", "redis": "down"}
    queue = {}
    # One session for both reads. The queue counts are best-effort: failing to
    # collect them says nothing about whether the database answers.
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            checks["db"] = "ok"
            try:
                queue = metrics.collect_queue_metrics(db)["counts"]
            except Exception as exc:  # noqa: BLE001
                log.warning("queue counts unavailable: %s",
                            observability.describe_exc(exc))
    except Exception:
        pass
    try:
        celery.backend.client.ping()
        checks["redis"] = "ok"
    except Exception:
        pass
    status = "ok" if checks["db"] == "ok" else "degraded"
    # Phase 14: the retention policy is part of the service's public contract,
    # so an operator can see what is kept and for how long without reading code.
    # Phase 18: the same reasoning for how much work is outstanding (collected
    # above, through the same session, and failing soft because a health check
    # must not be the thing that 500s).
    return {"status": status, "service": "findback-api", **checks,
            "retention": retention.retention_policy(), "queue": queue}

@app.get("/")
def root():
    return {"service": "FindBack", "docs": "/docs", "health": "/health"}
