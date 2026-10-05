"""Shared test setup: no test may reach a real AI provider.

Keys in a developer's .env would otherwise make test_extractor.py hit the
network, so every provider setting is cleared unless a test sets it explicitly.

This also restores the service modules after every test. Several tests stub
`embedder.embed_many` or `extractor.extract_brief` by assigning onto the
module, which outlives the test and silently changes every later one -- a
failure that is invisible until an unrelated test breaks.
"""
import os
import re
from functools import wraps

import pytest
from sqlalchemy.engine import make_url

from app import env


def require_test_database(url, setting):
    try:
        name = make_url(url).database or ""
    except Exception:
        raise pytest.UsageError(f"{setting} must be a valid test database URL") from None
    if not re.fullmatch(r"(?:fb_.+|test|test_.+|.+_test)", name):
        raise pytest.UsageError(
            f"Refusing {setting} database {name!r}; use fb_* or a test database")


# Load once, then clear leaked settings before test modules capture globals.
env.load_dotenv()
test_url = os.environ.get("TEST_DATABASE_URL", "").strip()
if not test_url:
    raise pytest.UsageError("TEST_DATABASE_URL is required; use a test database")
require_test_database(test_url, "TEST_DATABASE_URL")
for key in ("DATABASE_URL", "DEV_AUTH_ENABLED", "JOB_MAX_ATTEMPTS"):
    os.environ.pop(key, None)

from app import database

database.DATABASE_URL = database.SYNC_URL = test_url

from alembic import command


def guard_migration(operation):
    @wraps(operation)
    def guarded(config, *args, **kwargs):
        # Match alembic/env.py's precedence, including overrides set by a test.
        resolved = os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))
        require_test_database(resolved, "Alembic target")
        return operation(config, *args, **kwargs)
    return guarded


for action in ("upgrade", "downgrade", "stamp"):
    setattr(command, action, guard_migration(getattr(command, action)))

from app.services import embedder, extractor, fetcher

AI_ENV_KEYS = (
    "AI_PROVIDER", "AI_MODEL", "GROQ_API_KEY", "GEMINI_API_KEY",
    "GOOGLE_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL",
    "OPENAI_COMPATIBLE_API_KEY", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL",
    "EXTRACTOR_MODEL", "EXTRACTOR_MAX_CHARS", "EMBED_MAX_CHARS", "AI_DEBUG",
)

# The attributes a test may replace. Restoring exactly these keeps the guard
# from touching anything else on those modules.
_PATCHABLE = {
    fetcher: ("fetch_content",),
    embedder: ("embed_many", "embed_text", "chunk_text",
               "chunk_text_with_timestamps", "embedding_model_name"),
    extractor: ("extract_brief", "extract_memory"),
}


@pytest.fixture(autouse=True)
def offline_providers(monkeypatch):
    for key in AI_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def restore_service_modules():
    """Put back anything a test replaced on a service module."""
    saved = [(module, name, getattr(module, name))
             for module, names in _PATCHABLE.items() for name in names]
    yield
    for module, name, original in saved:
        setattr(module, name, original)
