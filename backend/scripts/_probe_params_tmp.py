"""TEMPORARY: verify parameter names against the live OpenAI-compatible layers.

Sends one fixed, tiny prompt per provider. Prints status codes and response
snippets only -- never a key, never user data.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app import env  # noqa: E402

SYSTEM = "Reply with JSON only."
USER = 'Return {"ok": true}'


def post(url: str, headers: dict, body: dict, label: str) -> None:
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(url, headers=headers, json=body)
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] ERROR {type(exc).__name__}: {exc}")
        return
    snippet = (r.text or "")[:400].replace("\n", " ")
    print(f"[{label}] HTTP {r.status_code}: {snippet}")
    if r.status_code == 200:
        try:
            data = r.json()
        except ValueError:
            return
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        print(f"    finish={choice.get('finish_reason')} "
              f"content={str(msg.get('content'))[:120]!r} "
              f"reasoning_keys={[k for k in msg if 'reason' in k.lower()]} "
              f"usage={json.dumps(usage)[:200]}")


def main() -> int:
    _, groq_key = env.first_env("GROQ_API_KEY")
    _, gem_key = env.first_env("GEMINI_API_KEY", "GOOGLE_API_KEY")
    _, nim_key = env.first_env("NVIDIA_API_KEY", "NIM_API_KEY")

    if groq_key:
        h = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
        post("https://api.groq.com/openai/v1/chat/completions", h, {
            "model": "openai/gpt-oss-20b",
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": USER}],
            "reasoning_effort": "low",
            "response_format": {"type": "json_object"},
            "max_tokens": 400,
        }, "groq low+json_object")
        post("https://api.groq.com/openai/v1/chat/completions", h, {
            "model": "openai/gpt-oss-20b",
            "messages": [{"role": "user", "content": USER}],
            "reasoning_effort": "minimal",
            "max_tokens": 200,
        }, "groq reasoning_effort=minimal (expect 400 if unsupported)")
        post("https://api.groq.com/openai/v1/chat/completions", h, {
            "model": "openai/gpt-oss-999b-not-real",
            "messages": [{"role": "user", "content": USER}],
            "max_tokens": 100,
        }, "groq model-not-found shape")
    else:
        print("[groq] no key")

    if gem_key:
        h = {"Authorization": f"Bearer {gem_key}", "Content-Type": "application/json"}
        base = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        post(base, h, {
            "model": "gemini-3.5-flash-lite",
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": USER}],
            "reasoning_effort": "minimal",
            "response_format": {"type": "json_object"},
            "max_tokens": 400,
        }, "gemini minimal+json_object")
        post(base, h, {
            "model": "gemini-3.5-flash-lite",
            "messages": [{"role": "user", "content": USER}],
            "reasoning_effort": "low",
            "max_tokens": 200,
        }, "gemini reasoning_effort=low")
        post(base, h, {
            "model": "gemini-3.5-flash-lite",
            "messages": [{"role": "user", "content": USER}],
            "max_completion_tokens": 200,
        }, "gemini max_completion_tokens")
        post(base, h, {
            "model": "gemini-3.5-flash-lite-does-not-exist-xyz",
            "messages": [{"role": "user", "content": USER}],
            "max_tokens": 100,
        }, "gemini model-not-found shape")
    else:
        print("[gemini] no key")

    # NVIDIA: catalog membership + free-endpoint flags, no key required to list.
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get("https://integrate.api.nvidia.com/v1/models")
        ids = [m.get("id") for m in r.json().get("data", []) if m.get("id")]
    except Exception as exc:  # noqa: BLE001
        print(f"[nvidia] catalog ERROR {type(exc).__name__}: {exc}")
        ids = []
    print(f"[nvidia] HTTP {r.status_code if ids else 'n/a'}, {len(ids)} models; "
          f"key present: {bool(nim_key)}")
    for want in ("nvidia/nemotron-3-nano-30b-a3b", "nvidia/nemotron-nano-3-30b-a3b",
                 "nvidia/nemotron-3.5-lightning-30b-a3b",
                 "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
                 "nvidia/nemotron-nano-vl-9b-a3b"):
        print(f"    {want}: {'LISTED' if want in ids else 'not listed'}")
    print("    nemotron* ids:", sorted(i for i in ids if "nemotron" in i))
    if nim_key:
        h = {"Authorization": f"Bearer {nim_key}", "Content-Type": "application/json"}
        post("https://integrate.api.nvidia.com/v1/chat/completions", h, {
            "model": "nvidia/nemotron-nano-3-30b-a3b",
            "messages": [{"role": "user", "content": USER}],
            "max_tokens": 300,
        }, "nvidia plain")
        post("https://integrate.api.nvidia.com/v1/chat/completions", h, {
            "model": "nvidia/nemotron-nano-3-30b-a3b",
            "messages": [{"role": "user", "content": USER}],
            "reasoning_effort": "low",
            "max_tokens": 300,
        }, "nvidia reasoning_effort=low")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
