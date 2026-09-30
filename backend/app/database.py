import os
import sys
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://findback:findback@localhost:5432/findback")
# Sync engine by design for the MVP (ARCHITECTURE previously overclaimed an async engine).
SYNC_URL = DATABASE_URL.replace("postgresql://", "postgresql://")

# backend/ â€” lets us resolve alembic.ini and the `app` package from any cwd
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

Base = declarative_base()

_engine: Optional[Engine] = None
_sessionmaker: Optional[sessionmaker] = None


def get_engine() -> Engine:
    """Built on first use so importing `app` never requires a DB driver."""
    global _engine
    if _engine is None:
        _engine = create_engine(SYNC_URL, pool_pre_ping=True)
    return _engine


def get_sessionmaker() -> sessionmaker:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _sessionmaker


def SessionLocal() -> Session:  # noqa: N802 - call-site compatible session factory
    return get_sessionmaker()()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def __getattr__(name: str):  # PEP 562: keep `from app.database import engine` working
    if name == "engine":
        return get_engine()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def ensure_extensions() -> None:
    import app.models  # noqa: F401  register mappers before create_all

    with get_engine().begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))


def create_all() -> None:
    """Create schema from models. Dev convenience only â€” Alembic is the source of truth."""
    ensure_extensions()
    Base.metadata.create_all(bind=get_engine())


def alembic_config():
    from alembic.config import Config

    if BACKEND_DIR not in sys.path:
        sys.path.insert(0, BACKEND_DIR)
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    # Absolute paths so this works from any cwd (uvicorn, celery, pytest, docker).
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    cfg.set_main_option("prepend_sys_path", BACKEND_DIR)
    cfg.set_main_option("sqlalchemy.url", SYNC_URL)
    return cfg


def upgrade_head() -> None:
    from alembic import command

    command.upgrade(alembic_config(), "head")


def init_db() -> str:
    """Bring the schema to head exactly one way, decided by SCHEMA_BOOTSTRAP.

    migrate (default) -> run `alembic upgrade head` in-process
    create            -> create_all from models (writes no migration history)
    none              -> do nothing (migrate manually / in CI)

    Previously startup unconditionally ran create_all, which collides with the
    Alembic baseline: on a DB whose tables already exist but whose
    `alembic_version` row is missing, create_all silently no-ops and the
    migration-only `tsv` generated column + GIN index are never created,
    silently disabling BM25 recall.
    """
    mode = os.getenv("SCHEMA_BOOTSTRAP", "migrate").strip().lower()
    if mode in ("none", "off", "skip"):
        return "skipped"
    if mode == "create":
        create_all()
        return "created"
    try:
        upgrade_head()
        return "migrated"
    except Exception as exc:  # fail loudly rather than serve an incomplete schema
        raise RuntimeError(
            f"Schema bootstrap via Alembic failed: {exc}\n"
            "Fix DB access/permissions, or set SCHEMA_BOOTSTRAP=create for a throwaway "
            "dev DB, or SCHEMA_BOOTSTRAP=none to migrate manually."
        ) from exc


