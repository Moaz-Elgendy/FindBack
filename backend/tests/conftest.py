"""Shared test setup: no test may reach a real AI provider.

Keys in a developer's .env would otherwise make test_extractor.py hit the
network, so every provider setting is cleared unless a test sets it explicitly.
"""
import pytest

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
