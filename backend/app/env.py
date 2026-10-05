"""Environment configuration, shared by every service.

Why this module exists
----------------------
1. It loads a local ``.env`` for developers running ``uvicorn`` directly. In
   Docker, docker-compose injects real env vars instead, and an existing value
   in ``os.environ`` always wins over the file.
2. It is the single place that reads the AI-related settings, so the embedding
   dimension used by the pgvector columns in ``app/models.py`` and the one
   requested from the provider API can never drift apart. A mismatch makes
   Postgres reject every insert with ``expected 1536 dimensions, not 3072``.

No third-party dependency: ``python-dotenv`` is not in requirements.txt, so the
~20 line parser below keeps ``pip install -r requirements.txt`` honest.
"""
import os
import pathlib

# Repo root is two levels above backend/app/env.py -> backend/, then FindBack/.
_HERE = pathlib.Path(__file__).resolve().parent
_CANDIDATES = (
    _HERE / ".env",                      # backend/app/.env
    _HERE.parent / ".env",               # backend/.env
    _HERE.parent.parent / ".env",        # FindBack/.env  (what docker-compose reads)
    pathlib.Path.cwd() / ".env",
)
_loaded = False

PLACEHOLDER_SUFFIXES = ("...", "xxx", "your-key-here", "changeme", "change-me")


def load_dotenv(force: bool = False) -> None:
    """Populate os.environ from the first .env found. Existing vars win."""
    global _loaded
    if _loaded and not force:
        return
    _loaded = True
    for path in _CANDIDATES:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Strip inline comments only for unquoted values.
            if value[:1] not in ("'", '"') and " #" in value:
                value = value.split(" #", 1)[0].rstrip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
        return  # first file found wins, mirroring docker-compose's single env_file


def get(name: str, default: str = "") -> str:
    load_dotenv()
    return (os.getenv(name) or default).strip()


def get_int(name: str, default: int) -> int:
    raw = get(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        print(f"[env] {name}={raw!r} is not an integer; falling back to {default}")
        return default


def get_bool(name: str, default: bool = False) -> bool:
    raw = get(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def get_float(name: str, default: float) -> float:
    raw = get(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        print(f"[env] {name}={raw!r} is not a number; falling back to {default}")
        return default


def is_placeholder(value: str) -> bool:
    """True for the fake values shipped in .env.example (``sk-...`` and friends).

    The original code special-cased only ``sk-...``; the suffix check covers
    ``gsk_...`` (Groq) and ``AIza...`` (Google) style examples too.
    """
    v = (value or "").strip()
    if not v:
        return True
    low = v.lower()
    if low in ("none", "off", "todo", "changeme", "change-me", "your-key-here"):
        return True
    return low.endswith(PLACEHOLDER_SUFFIXES)


def first_env(*names: str) -> tuple[str, str]:
    """Return (name, value) of the first env var that holds a real key."""
    load_dotenv()
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value and not is_placeholder(value):
            return name, value
    return "", ""


# --- AI settings -------------------------------------------------------------
# Provider ids accepted by AI_PROVIDER / EMBEDDING_PROVIDER.
PROVIDERS = ("groq", "gemini", "openai", "openai_compatible")

# The column width in models.py/migration 0001 is 1536. Keep this default so an
# existing database stays valid; see docs/OPERATIONS.md before changing it.
EMBEDDING_DIMS = get_int("EMBEDDING_DIMS", 1536)
# Gemini's batchEmbedContents accepts at most 100 contents per request; the
# OpenAI-style endpoints take more but a small batch keeps a failed ingest
# request from throwing away a lot of work.
EMBEDDING_BATCH_SIZE = max(1, min(100, get_int("EMBEDDING_BATCH_SIZE", 96)))
AI_TIMEOUT = get_float("AI_TIMEOUT_SECONDS", 45.0)
# Total attempts, not retries-after-the-first: one try plus retries.
AI_MAX_ATTEMPTS = max(1, get_int("AI_MAX_RETRIES", 3))

# Phase 14. Hours a fetched page is kept before it is discarded. Zero means
# "drop it as soon as the pipeline finishes", which is the default: the stages
# need the raw text only while they run, and afterwards it is a verbatim copy
# of something the user read. See docs/OPERATIONS.md.
RAW_TEXT_RETENTION_HOURS = max(0, get_int("RAW_TEXT_RETENTION_HOURS", 0))
# Hard cap on the extractor's completion, so a rambling model cannot hold an
# ingest request open indefinitely. ~900 tokens is ample for a 6-field object.
EXTRACTOR_MAX_TOKENS = get_int("EXTRACTOR_MAX_TOKENS", 900)
# Log full request/response bodies. Never enabled by default: they hold text
# the user just photographed.
DEBUG = get_bool("AI_DEBUG", False)


def mask_key(value: str) -> str:
    """Show enough of a key to identify it without putting it in a log."""
    v = (value or "").strip()
    if not v:
        return "(unset)"
    if len(v) <= 8:
        return v[:2] + "***"
    return f"{v[:5]}...{v[-2:]} ({len(v)} chars)"


# Env vars that must never appear in a log line, a traceback, or an API error
# response. main.py installs this filter on every log handler. DATABASE_URL is
# deliberately absent: masking it whole would hide the host operators need.
SECRET_ENV_KEYS = (
    "GROQ_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
    "OPENAI_COMPATIBLE_API_KEY", "API_SECRET_KEY", "SUPABASE_JWT_SECRET",
    "SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY", "FIRECRAWL_API_KEY",
    "RESEND_API_KEY", "STT_CLOUD_API_KEY",
)
# Provider key env vars, in the order a reader would want to see them.
KEY_ENV_ORDER = ("GROQ_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
                 "OPENAI_API_KEY", "OPENAI_COMPATIBLE_API_KEY")


def missing_keys() -> list[str]:
    """Provider keys that are unset or still the shipped placeholder."""
    load_dotenv()
    return [name for name in KEY_ENV_ORDER if is_placeholder(os.getenv(name) or "")]


def warnings() -> list[str]:
    """Configuration problems worth printing at startup, but not fatal."""
    load_dotenv()
    out: list[str] = []
    if len(missing_keys()) == len(KEY_ENV_ORDER):
        out.append("no provider API key is configured (checked "
                   + ", ".join(KEY_ENV_ORDER) + "): extraction uses the offline "
                   "heuristic and search falls back to keyword mode")
    if EMBEDDING_DIMS != 1536:
        out.append(f"EMBEDDING_DIMS={EMBEDDING_DIMS} but models.py declares Vector(1536): "
                   "every insert will fail until the column is migrated")
    provider = get("AI_PROVIDER").lower()
    if provider and provider not in PROVIDERS:
        out.append(f"AI_PROVIDER={provider!r} is not one of {', '.join(PROVIDERS)}")
    embed_provider = get("EMBEDDING_PROVIDER").lower()
    if embed_provider == "groq":
        out.append("EMBEDDING_PROVIDER=groq is a poor fit: nomic-embed-text is ~137-dim "
                   "and rejects empty input, while the column is 1536-wide")
    return out


def summary() -> str:
    """One-line description of the resolved AI config, for the startup log."""
    from app.services import ai  # local import: ai imports this module

    lines = []
    chat = ai.chat_config()
    lines.append(f"chat={'off (heuristic)' if chat is None else f'{chat.provider}:{chat.model}'}")
    embed = ai.embedding_config()
    lines.append(f"embed={'off (keyword search)' if embed is None else f'{embed.label}@{embed.dims}d'}")
    return ", ".join(lines)

