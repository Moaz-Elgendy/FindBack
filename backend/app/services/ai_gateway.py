"""The AI gateway (Phase 14).

Three capabilities, three interfaces, one place the rest of the app depends on:

    StructuredGenerator   produce a JSON object matching a schema
    Extractor             turn saved content into a Brief
    Embedder              turn text into vectors

Everything above this module speaks in these terms. Everything below it -- HTTP
transport, provider quirks, model ids, key names -- lives in the provider adapter
(`app.services.ai`) and nowhere else. Swapping providers is therefore a change in
one module plus a registry entry, not a change in the extractor.

The current provider stays the default. A fake can be installed by assigning to
`set_gateway`, which is how the tests prove the swap works without a network.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from app.schemas import Brief
from app.services import ai, metrics, observability, privacy

log = logging.getLogger("findback.gateway")


@runtime_checkable
class StructuredGenerator(Protocol):
    """Produce a JSON object. Used for anything with a schema behind it."""

    async def generate_json(self, system_prompt: str, user_prompt: str, *,
                            temperature: float = 0.0) -> dict[str, Any]:
        ...


@runtime_checkable
class Embedder(Protocol):
    """Turn text into vectors. `task` separates document space from query space."""

    async def embed(self, texts: list[str], *,
                    task: str = "document") -> list[list[float] | None]:
        ...

    async def embed_one(self, text: str, *,
                        task: str = "document") -> list[float] | None:
        ...

    def model_name(self) -> str:
        ...


@runtime_checkable
class Extractor(Protocol):
    """Turn saved content into a Brief."""

    async def extract(self, raw_text: str, url_title: str = "",
                      url: str = "") -> Brief:
        ...


class ProviderAdapter:
    """The default implementation, delegating to the existing HTTP provider.

    This is the only class that knows a provider exists. The current provider
    keeps working exactly as before; nothing about its behaviour changes here.
    """

    name = "default"

    async def generate_json(self, system_prompt: str, user_prompt: str, *,
                            temperature: float = 0.0) -> dict[str, Any]:
        return await ai.chat_json(system_prompt, user_prompt,
                                  temperature=temperature)

    async def embed(self, texts, *, task: str = "document"):
        return await ai.embed_texts(texts, task=task)

    async def embed_one(self, text: str, *, task: str = "document"):
        return await ai.embed_text(text, task=task)

    def model_name(self) -> str:
        cfg = ai.embedding_config()
        return cfg.model if cfg else ""


class BriefExtractor:
    """The extraction *policy*, expressed against the Extractor interface.

    Kept apart from the provider so that "how we prompt" and "who we talk to"
    can change for different reasons.
    """

    def __init__(self, gateway: "Gateway"):
        self.gateway = gateway

    async def extract(self, raw_text: str, url_title: str = "",
                      url: str = "") -> Brief:
        from app.services.extractor import extract_brief_through

        return await extract_brief_through(self.gateway, raw_text, url_title, url)


class Gateway:
    """What the rest of the app depends on. Swappable, never built directly."""

    def __init__(self, adapter=None):
        self.adapter = adapter or ProviderAdapter()

    async def generate_json(self, system_prompt: str, user_prompt: str, *,
                            temperature: float = 0.0) -> dict[str, Any]:
        # Register the user's own words so the privacy filter can redact them if
        # anything below us decides to log them.
        privacy.register_private(user_prompt)
        # Phase 18: counted at the gateway, not in the provider adapter, so the
        # number is the same whichever provider is configured -- and a fake
        # installed in a test is counted too.
        metrics.ai_requests.inc(labels={"outcome": "attempt"})
        try:
            return await self.adapter.generate_json(system_prompt, user_prompt,
                                                    temperature=temperature)
        except Exception as exc:  # noqa: BLE001 - re-raised immediately
            metrics.ai_failures.inc(labels={"outcome": "raised"})
            observability.log_event(
                "ai.call_failed", level=logging.WARNING, stage="generate_json",
                status="failed")
            log.warning("[gateway] chat call failed: %s",
                        observability.describe_exc(exc))
            raise

    async def embed(self, texts, *, task: str = "document"):
        for text in texts:
            privacy.register_private(text)
        metrics.embedding_requests.inc(labels={"outcome": "batch"})
        try:
            return await self.adapter.embed(texts, task=task)
        except Exception as exc:  # noqa: BLE001 - re-raised immediately
            # Not a separate failure counter: an embedding failure and a chat
            # failure have the same operational meaning, which is "the provider
            # refused us", and `ai_failures` already counts that.
            metrics.ai_failures.inc(labels={"outcome": "raised"})
            observability.log_event(
                "ai.embed_failed", level=logging.WARNING, stage="embed",
                status="failed")
            log.warning("[gateway] embedding call failed: %s",
                        observability.describe_exc(exc))
            raise

    async def embed_one(self, text: str, *, task: str = "document"):
        privacy.register_private(text)
        metrics.embedding_requests.inc(labels={"outcome": "single"})
        try:
            return await self.adapter.embed_one(text, task=task)
        except Exception as exc:  # noqa: BLE001 - re-raised immediately
            metrics.ai_failures.inc(labels={"outcome": "raised"})
            observability.log_event(
                "ai.embed_failed", level=logging.WARNING, stage="embed_one",
                status="failed")
            log.warning("[gateway] embedding call failed: %s",
                        observability.describe_exc(exc))
            raise

    def model_name(self) -> str:
        return self.adapter.model_name()

    async def extract_brief(self, raw_text: str, url_title: str = "",
                            url: str = "") -> Brief:
        privacy.register_private(raw_text)
        return await BriefExtractor(self).extract(raw_text, url_title, url)


_GATEWAY = Gateway()


def get_gateway() -> Gateway:
    """The gateway in use. Callers depend on this, never on a provider."""
    return _GATEWAY


def set_gateway(gateway: Gateway) -> Gateway:
    """Install a gateway. Returns the previous one so a test can restore it."""
    global _GATEWAY
    previous = _GATEWAY
    _GATEWAY = gateway
    return previous


_PROVIDERS: dict[str, Any] = {}


def register_provider(name: str, factory) -> None:
    """Register a provider factory under a name, selectable from config.

    This is the whole of "swapping providers": the factory returns an object
    implementing the interfaces above. Nothing else in the codebase changes.
    """
    _PROVIDERS[name] = factory


def provider_from_config(name: str = "") -> Gateway:
    """Build a gateway from configuration, falling back to the current provider."""
    if not name:
        return Gateway()
    factory = _PROVIDERS.get(name)
    if factory is None:
        log.warning("[gateway] no provider registered as %r; using the default", name)
        return Gateway()
    return Gateway(adapter=factory())