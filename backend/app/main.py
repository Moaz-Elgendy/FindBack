import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text
from app.database import SessionLocal, init_db
from app.celery_app import celery
from fastapi.middleware.cors import CORSMiddleware
from app.routers import ingest, search, items

log = logging.getLogger("findback.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
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
