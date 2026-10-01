import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text
from app.database import SessionLocal, init_db
from app.celery_app import celery
from fastapi.middleware.cors import CORSMiddleware
from app.routers import ingest, search, items
from app import env

log = logging.getLogger("findback.api")


class SecretMask(logging.Filter):
    """Redact configured API keys from every record that passes through.

    Provider and httpx errors quote the request they failed, and the key sits in
    the Authorization header of that request. Filtering at the handler catches
    records from uvicorn and sqlalchemy too, not just our own loggers.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text_ = record.getMessage()
        except Exception:  # a malformed % formatting must not lose the record
            return True
        for name in env.SECRET_ENV_KEYS:
            secret = env.get(name)
            if secret and len(secret) > 5 and secret in text_:
                text_ = text_.replace(secret, env.mask_key(secret))
        record.msg = text_
        record.args = ()
        return True


def _install_secret_masking() -> None:
    """Attach one SecretMask to every handler that could print a secret.

    uvicorn configures its own loggers with propagate=False, so filtering the
    root logger alone would miss exactly the access/error logs we care about.
    """
    mask = SecretMask()
    for name in ("", "findback", "uvicorn", "uvicorn.error", "uvicorn.access",
                 "sqlalchemy.engine"):
        for handler in logging.getLogger(name).handlers:
            if mask not in handler.filters:
                handler.addFilter(mask)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _install_secret_masking()
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

app.include_router(ingest.router)
app.include_router(search.router)
app.include_router(items.router)

@app.get("/health")
def health():
    checks = {"db": "down", "redis": "down"}
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:
        pass
    try:
        celery.backend.client.ping()
        checks["redis"] = "ok"
    except Exception:
        pass
    status = "ok" if checks["db"] == "ok" else "degraded"
    return {"status": status, "service": "findback-api", **checks}

@app.get("/")
def root():
    return {"service": "FindBack", "docs": "/docs", "health": "/health"}
