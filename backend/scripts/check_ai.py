"""Connectivity check for the AI provider(s) FindBack resolves from .env.

Run from ``backend/`` so the ``app`` package is importable:

    python scripts/check_ai.py                # resolved config + a live chat ping
    python scripts/check_ai.py --list-models   # chat model ids the provider offers
    python scripts/check_ai.py --embeddings    # embed a probe, report the vector width

Exit code is 0 when every requested check passed and 1 otherwise, so an operator
or a CI job can gate on it. Only fixed probe strings are sent - never user data.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Running a script puts its own directory (backend/scripts) on sys.path, not the
# cwd, so add backend/ explicitly to make `import app` work from any directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app import env  # noqa: E402
from app.services import ai  # noqa: E402

PROBE = "FindBack connectivity probe. Reply with the single word: ok"


def _models_url(chat_url: str) -> str:
    """The /models endpoint that sits beside a chat-completions URL."""
    suffix = "/chat/completions"
    base = chat_url[: -len(suffix)] if chat_url.endswith(suffix) else chat_url.rstrip("/")
    return f"{base}/models"


async def _list_models(cfg: ai.ChatConfig) -> int:
    url = _models_url(cfg.url)
    try:
        async with httpx.AsyncClient(timeout=env.AI_TIMEOUT) as client:
            response = await client.get(url, headers=dict(cfg.headers))
    except httpx.HTTPError as exc:
        print(f"  ! could not reach {url}: {type(exc).__name__}: {exc}")
        return 1
    if response.status_code != 200:
        print(f"  ! {url} returned HTTP {response.status_code}: {response.text[:200]}")
        return 1
    try:
        payload = response.json()
    except ValueError:
        print(f"  ! {url} did not return JSON")
        return 1
    ids = [item.get("id") for item in payload.get("data", []) if isinstance(item, dict)]
    ids = [model_id for model_id in ids if model_id]
    if not ids:
        print(f"  ! no model ids in the response; top-level keys: {sorted(payload)[:8]}")
        return 1
    for model_id in sorted(ids):
        print(f"    {model_id}")
    print(f"  ({len(ids)} models; set EXTRACTOR_MODEL to one of these)")
    return 0


async def _ping_chat(cfg: ai.ChatConfig) -> int:
    try:
        text = await ai.chat_text("You are a connectivity probe.", PROBE)
    except ai.AIError as exc:
        print(f"  ! {type(exc).__name__}: {exc}")
        return 1
    print(f"  ok: {cfg.provider} replied {text[:80]!r}")
    return 0


async def _check_embeddings() -> int:
    embed_cfg = ai.embedding_config()
    if embed_cfg is None:
        print("  embeddings: off (no embedding-capable provider is configured)")
        print("  set GEMINI_API_KEY or OPENAI_API_KEY, or EMBEDDING_PROVIDER explicitly")
        return 1
    vectors = await ai.embed_texts(["FindBack embedding probe"])
    vector = vectors[0] if vectors else None
    if not vector:
        print(f"  ! {embed_cfg.label} returned no vector (see the log lines above)")
        return 1
    print(f"  ok: {embed_cfg.label} returned {len(vector)} dimensions")
    if len(vector) != embed_cfg.dims:
        print(f"  ! width {len(vector)} does not match the column ({embed_cfg.dims})")
        return 1
    return 0


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Check the FindBack AI provider configuration.")
    parser.add_argument("--list-models", action="store_true",
                        help="list chat model ids the configured provider offers")
    parser.add_argument("--embeddings", action="store_true",
                        help="embed a probe string and report the vector width")
    args = parser.parse_args(argv[1:])

    print(f"config: {env.summary()}")
    for problem in env.warnings():
        print(f"warning: {problem}")

    chat_cfg = ai.chat_config()
    if chat_cfg is None:
        print("chat: off - no provider key is configured (extraction uses the "
              "offline heuristic)")
        # An embeddings-only check can still succeed without a chat provider.
        if args.embeddings:
            return await _check_embeddings()
        return 1

    print(f"chat: {chat_cfg.provider} model={chat_cfg.model}")
    status = 0
    if args.list_models:
        status |= await _list_models(chat_cfg)
    else:
        status |= await _ping_chat(chat_cfg)
    if args.embeddings:
        status |= await _check_embeddings()
    return 1 if status else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))
