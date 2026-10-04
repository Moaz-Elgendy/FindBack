"""Model selection and key-name resolution are configuration, not code.

Three things this pins down:

  * with nothing set, each provider gets its built-in default -- the behaviour
    that existed before any of these variables were configurable;
  * an override in the environment is honoured, per role, and the chat role
    still accepts the older EXTRACTOR_MODEL name so an existing .env keeps
    working;
  * GEMINI_API_KEY and GOOGLE_API_KEY are two spellings of one Google AI Studio
    key, both of which must configure the gemini provider on their own.

No network: every assertion is about the resolved configuration, which is what
the transport is handed.

    python -m pytest tests/test_ai_model_selection.py -q
"""
from app.services import ai

import pytest

# Obviously-fake keys. The shape only has to survive `env.is_placeholder`,
# which rejects empty values, a few literal words, and the shipped "..."-style
# examples. Nothing here is ever sent anywhere.
FAKE_GEMINI_KEY = "AIzaFakeKeyForTestsOnly000"
FAKE_GOOGLE_KEY = "AIzaFakeAliasKeyForTests00"
FAKE_GROQ_KEY = "gsk_FakeKeyForTestsOnly00000"
FAKE_OPENAI_KEY = "sk-FakeKeyForTestsOnly0000"


@pytest.fixture
def every_provider_key(monkeypatch):
    """A usable key for every provider, so only the model can decide."""
    monkeypatch.setenv("GROQ_API_KEY", FAKE_GROQ_KEY)
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_GEMINI_KEY)
    monkeypatch.setenv("GOOGLE_API_KEY", FAKE_GOOGLE_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI_KEY)
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", FAKE_OPENAI_KEY)


# --- defaults when nothing is set -----------------------------------------

@pytest.mark.parametrize("provider,expected", sorted(ai.CHAT_MODEL_DEFAULTS.items()))
def test_the_chat_role_defaults_to_the_providers_own_model(
        monkeypatch, every_provider_key, provider, expected):
    monkeypatch.setenv("AI_PROVIDER", provider)

    assert ai.chat_model(provider) == expected
    assert ai.chat_config().model == expected, (
        "the resolved default must be what the transport is actually sent")


@pytest.mark.parametrize("provider,expected", sorted(ai.EMBED_MODEL_DEFAULTS.items()))
def test_the_embedding_role_defaults_to_the_providers_own_model(
        monkeypatch, every_provider_key, provider, expected):
    if provider not in ai.EMBEDDING_CAPABLE:
        pytest.skip(f"{provider} is not embedding-capable in this build")
    monkeypatch.setenv("AI_PROVIDER", provider)
    monkeypatch.setenv("EMBEDDING_PROVIDER", provider)

    assert ai.embedding_model(provider) == expected
    assert ai.embedding_config().model == expected
# --- an override is respected ---------------------------------------------

def test_the_chat_role_takes_its_model_from_the_environment(monkeypatch):
    """The point of the task: pin a different Flash version without code."""
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_GEMINI_KEY)
    monkeypatch.setenv("AI_MODEL", "gemini-3.5-flash-preview")

    assert ai.chat_model("gemini") == "gemini-3.5-flash-preview"
    assert ai.chat_config().model == "gemini-3.5-flash-preview"
    assert ai.embedding_model("gemini") == ai.EMBED_MODEL_DEFAULTS["gemini"], (
        "the two roles must not read each other's variable")


def test_the_embedding_role_takes_its_model_from_the_environment(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_GEMINI_KEY)
    monkeypatch.setenv("AI_MODEL", "gemini-3.5-flash-preview")
    monkeypatch.setenv("EMBEDDING_MODEL", "gemini-embedding-005")

    assert ai.embedding_model("gemini") == "gemini-embedding-005"
    cfg = ai.embedding_config()
    assert cfg.model == "gemini-embedding-005"
    assert cfg.url.endswith("models/gemini-embedding-005"), (
        "the model id has to reach the URL, not just the config object")
    assert ai.chat_config().model == "gemini-3.5-flash-preview"


def test_the_older_extractor_model_name_still_works(monkeypatch):
    """An .env written against the old name must keep the model it asked for."""
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_GEMINI_KEY)
    monkeypatch.setenv("EXTRACTOR_MODEL", "gemini-2.0-flash")

    assert ai.chat_model("gemini") == "gemini-2.0-flash"
    assert ai.chat_config().model == "gemini-2.0-flash"


def test_the_new_name_wins_when_both_are_set(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_GEMINI_KEY)
    monkeypatch.setenv("AI_MODEL", "gemini-3.5-flash-preview")
    monkeypatch.setenv("EXTRACTOR_MODEL", "gemini-2.0-flash")

    assert ai.chat_model("gemini") == "gemini-3.5-flash-preview"


def test_a_blank_model_variable_falls_back_to_the_default(monkeypatch):
    """An empty value in .env means "unset", not "use a model named ''"."""
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_GEMINI_KEY)
    monkeypatch.setenv("AI_MODEL", "")

    assert ai.chat_model("gemini") == ai.CHAT_MODEL_DEFAULTS["gemini"]
# --- the two Google key names ----------------------------------------------

def test_gemini_api_key_configures_the_provider_on_its_own(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_GEMINI_KEY)

    cfg = ai.chat_config()
    assert cfg is not None, "the documented name must work by itself"
    assert cfg.provider == "gemini"
    assert cfg.api_key == FAKE_GEMINI_KEY
    assert ai.embedding_config().api_key == FAKE_GEMINI_KEY, (
        "one key serves both roles; it is not chat-only or embeddings-only")


def test_google_api_key_is_an_accepted_alias_on_its_own(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", FAKE_GOOGLE_KEY)

    cfg = ai.chat_config()
    assert cfg is not None, "the alias must work by itself, not only alongside"
    assert cfg.provider == "gemini"
    assert cfg.api_key == FAKE_GOOGLE_KEY
    assert ai.embedding_config().api_key == FAKE_GOOGLE_KEY


def test_the_documented_name_wins_when_both_google_spellings_are_set(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_GEMINI_KEY)
    monkeypatch.setenv("GOOGLE_API_KEY", FAKE_GOOGLE_KEY)

    assert ai.chat_config().api_key == FAKE_GEMINI_KEY


def test_the_alias_still_counts_for_autodetect(monkeypatch):
    """Unset AI_PROVIDER + only the alias set must still pick gemini."""
    monkeypatch.setenv("GOOGLE_API_KEY", FAKE_GOOGLE_KEY)

    assert ai.chat_config().provider == "gemini"


def test_a_placeholder_alias_does_not_configure_the_provider(monkeypatch):
    """The shipped "AIza..." example must not count as a real key."""
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza...")

    assert ai.chat_config() is None




def test_there_is_exactly_one_variable_per_model_role():
    """Two roles, two variables, no third name quietly deciding a model."""
    assert ai.CHAT_MODEL_ENV == "AI_MODEL"
    assert ai.EMBEDDING_MODEL_ENV == "EMBEDDING_MODEL"
    assert set(ai.CHAT_MODEL_DEFAULTS) == set(ai.EMBED_MODEL_DEFAULTS), (
        "every provider that can chat should also have an embedding default")
