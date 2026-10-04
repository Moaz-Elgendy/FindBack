"""Config resolution and HTTP failure handling, driven by httpx.MockTransport."""
import asyncio
import json

import httpx
import pytest

from app import env
from app.services import ai


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def instant(monkeypatch):
    """Zero the backoff so retry paths cost no wall-clock time."""
    monkeypatch.setattr(ai, "BACKOFF_BASE", 0.0)
    monkeypatch.setattr(ai, "BACKOFF_CAP", 0.0)


@pytest.fixture
def install(monkeypatch):
    """Point _post_json at a mock transport and record every request body."""
    def _install(handler):
        seen = []

        def route(request):
            seen.append(json.loads(request.content))
            return handler(request)

        monkeypatch.setattr(ai, "_TRANSPORT", httpx.MockTransport(route))
        return seen
    return _install


def ok(body):
    return httpx.Response(200, json=body)


# --- configuration resolution -------------------------------------------------

def test_no_keys_means_no_chat_provider(offline_providers):
    assert ai.chat_config() is None


def test_groq_key_selects_groq_and_bearer_auth(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_realkey123456")
    cfg = ai.chat_config()
    assert cfg.provider == "groq"
    assert cfg.url.endswith("/chat/completions")
    assert cfg.headers["Authorization"] == "Bearer gsk_realkey123456"


def test_placeholder_key_is_not_treated_as_configuration(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "your-key-here")
    assert ai.chat_config() is None
    assert any("GROQ_API_KEY" in problem for problem in env.warnings())


def test_explicit_provider_without_its_key_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_realkey123456")
    assert ai.chat_config() is None


def test_unknown_provider_name_is_reported_and_ignored(monkeypatch, caplog):
    monkeypatch.setenv("AI_PROVIDER", "mistral")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_realkey123456")
    assert ai.chat_config() is None
    assert "mistral" in caplog.text


def test_extractor_model_override_wins(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_realkey123456")
    monkeypatch.setenv("EXTRACTOR_MODEL", "llama-4-scout")
    assert ai.chat_config().model == "llama-4-scout"


def test_openai_compatible_uses_openai_base_url(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai_compatible")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-localkey123")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://ollama:11434/v1")
    assert ai.chat_config().url == "http://ollama:11434/v1/chat/completions"


def test_groq_chat_setup_picks_gemini_for_embeddings(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_realkey123456")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaRealKey123456")
    cfg = ai.embedding_config()
    assert cfg.provider == "gemini"
    assert cfg.style == "gemini_native"
    assert cfg.url.endswith(f"models/{cfg.model}")


def test_groq_only_setup_disables_embeddings_rather_than_mis_sizing_them(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_realkey123456")
    assert ai.embedding_config() is None


def test_embedding_dimensions_come_from_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaRealKey123456")
    monkeypatch.setattr(env, "EMBEDDING_DIMS", 768)
    assert ai.embedding_config().dims == 768


def test_gemini_truncation_renormalises_below_3072(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaRealKey123456")
    monkeypatch.setattr(env, "EMBEDDING_DIMS", 1536)
    assert ai.embedding_config().normalize is True
    monkeypatch.setattr(env, "EMBEDDING_DIMS", 3072)
    assert ai.embedding_config().normalize is False


def test_embedding_label_names_provider_and_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaRealKey123456")
    cfg = ai.embedding_config()
    assert cfg.label == f"gemini:{cfg.model}"


# --- _post_json: transport, retry, and request-shape self-heal ----------------

def test_retries_a_rate_limit_then_succeeds(install, instant):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return ok({"choices": [{"message": {"content": "done"}}]})

    seen = install(handler)
    data = run(ai._post_json("https://x/y", {}, {"model": "m"}, provider="groq"))
    assert len(seen) == 2
    assert data["choices"][0]["message"]["content"] == "done"


def test_auth_failure_is_not_retried_and_carries_a_hint(install, instant):
    seen = install(lambda request: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(ai.AIHTTPError) as excinfo:
        run(ai._post_json("https://x/y", {}, {"model": "m"}, provider="groq"))
    assert len(seen) == 1
    assert "check the key" in str(excinfo.value)


def test_a_400_naming_a_field_drops_it_and_retries(install, instant):
    def handler(request):
        body = json.loads(request.content)
        if "response_format" in body:
            return httpx.Response(
                400, json={"error": {"message": 'Unknown name "response_format"'}})
        return ok({"choices": [{"message": {"content": "ok"}}]})

    seen = install(handler)
    payload = {"model": "m", "response_format": {"type": "json_object"}}
    data = run(ai._post_json("https://x/y", {}, payload, provider="groq"))
    assert len(seen) == 2
    assert "response_format" not in seen[1]  # the offending field is gone
    assert data["choices"][0]["message"]["content"] == "ok"


def test_transport_error_becomes_unreachable_after_retries(install, instant):
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        raise httpx.ConnectError("no route to host")

    install(handler)
    with pytest.raises(ai.AIUnreachableError):
        run(ai._post_json("https://x/y", {}, {"model": "m"}, provider="groq"))
    assert attempts["n"] == env.AI_MAX_ATTEMPTS


def test_200_with_a_non_json_body_is_a_protocol_error(install, instant):
    install(lambda request: httpx.Response(200, text="<html>gateway error</html>"))
    with pytest.raises(ai.AIProtocolError):
        run(ai._post_json("https://x/y", {}, {"model": "m"}, provider="groq"))


def test_200_wrapping_an_error_envelope_is_a_protocol_error(install, instant):
    install(lambda request: ok({"error": {"message": "model overloaded"}}))
    with pytest.raises(ai.AIProtocolError) as excinfo:
        run(ai._post_json("https://x/y", {}, {"model": "m"}, provider="groq"))
    assert "overloaded" in str(excinfo.value)


# --- chat_json: the JSON correction retry ------------------------------------

def test_chat_json_recovers_when_the_model_returns_bad_json(monkeypatch, install, instant):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_realkey123456")
    responses = iter([
        ok({"choices": [{"message": {"content": "not json at all"}}]}),
        ok({"choices": [{"message": {"content": '{"summary": "fixed"}'}}]}),
    ])
    seen = install(lambda request: next(responses))
    assert run(ai.chat_json("sys", "user")) == {"summary": "fixed"}
    assert len(seen) == 2
    # The second request re-asks with a correction appended and JSON mode off.
    assert len(seen[1]["messages"]) == 3
    assert "response_format" not in seen[1]


def test_chat_json_without_a_provider_raises_config_error(offline_providers):
    with pytest.raises(ai.AIConfigError):
        run(ai.chat_json("sys", "user"))
