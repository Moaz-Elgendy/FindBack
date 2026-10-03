"""Shared test setup: no test may reach a real AI provider.

Keys in a developer's .env would otherwise make test_extractor.py hit the
network, so every provider setting is cleared unless a test sets it explicitly.

This also restores the service modules after every test. Several tests stub
`embedder.embed_many` or `extractor.extract_brief` by assigning onto the
module, which outlives the test and silently changes every later one -- a
failure that is invisible until an unrelated test breaks.
"""
import pytest

from app.services import embedder, extractor

AI_ENV_KEYS = (
    "AI_PROVIDER", "GROQ_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_COMPATIBLE_API_KEY",
    "EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "EXTRACTOR_MODEL",
    "EXTRACTOR_MAX_CHARS", "EMBED_MAX_CHARS", "AI_DEBUG",
)

# The attributes a test may replace. Restoring exactly these keeps the guard
# from touching anything else on those modules.
_PATCHABLE = {
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

AI_ENV_KEYS = (
    "AI_PROVIDER", "GROQ_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_COMPATIBLE_API_KEY",
    "EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "EXTRACTOR_MODEL",
    "EXTRACTOR_MAX_CHARS", "EMBED_MAX_CHARS", "AI_DEBUG",
)


@pytest.fixture(autouse=True)
def offline_providers(monkeypatch):
    for key in AI_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
