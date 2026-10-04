"""TEMPORARY probe: confirm model ids against each provider's list-models endpoint.

Reads keys from .env at runtime, never prints a key, never sends a prompt.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app import env  # noqa: E402

WANT = {
    "groq": ["gpt-oss", "llama", "qwen"],
    "gemini": ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.1-flash-lite",
               "gemini-2.5-flash-lite", "gemini-embedding"],
    "nvidia": ["nemotron", "nvidia/", "meta/llama"],
}


def ids_from(data) -> list[str]:
    if isinstance(data, dict):
        models = data.get("models") or data.get("data") or []
    else:
        models = data
    out = []
    for m in models:
        if isinstance(m, str):
            out.append(m)
        elif isinstance(m, dict):
            out.append(str(m.get("id") or m.get("name") or ""))
    return [x for x in out if x]


def probe(name: str, url: str, headers: dict) -> None:
    key_present = bool(headers)
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(url, headers=headers)
    except Exception as exc:  # noqa: BLE001
        print(f"[{name}] ERROR {type(exc).__name__}: {exc}")
        return
    print(f"[{name}] HTTP {r.status_code} from {url}")
    if r.status_code != 200:
        print(f"[{name}] body: {r.text[:300]}")
        return
    try:
        data = r.json()
    except ValueError:
        print(f"[{name}] non-JSON body: {r.text[:200]}")
        return
    ids = ids_from(data)
    print(f"[{name}] {len(ids)} model ids listed; key present: {key_present}")
    for needle in WANT.get(name, []):
        hits = [i for i in ids if needle.lower() in i.lower()]
        print(f"    contains {needle!r}: {hits[:12] if hits else 'NONE'}")
    if name == "gemini":
        print(f"    all ids (first 60): {sorted(ids)[:60]}")
    if name == "nvidia":
        print(f"    all ids (first 80): {sorted(ids)[:80]}")


def main() -> int:
    _, groq_key = env.first_env("GROQ_API_KEY")
    _, gem_key = env.first_env("GEMINI_API_KEY", "GOOGLE_API_KEY")
    _, nim_key = env.first_env("NVIDIA_API_KEY", "NIM_API_KEY")

    probe("groq", "https://api.groq.com/openai/v1/models",
          {"Authorization": f"Bearer {groq_key}"} if groq_key else {})
    probe("gemini", "https://generativelanguage.googleapis.com/v1beta/models",
          {"x-goog-api-key": gem_key} if gem_key else {})
    probe("nvidia", "https://integrate.api.nvidia.com/v1/models",
          {"Authorization": f"Bearer {nim_key}"} if nim_key else {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
