"""Keeping private content out of ordinary logs (Phase 14).

A saved link is the user's content. Anything derived from it -- the fetched
text, the transcript, the model's structured answer about it -- is the user's
content too, and none of it belongs in an application log that is shipped,
grepped, or kept for months.

Two mechanisms, because either alone is insufficient:

    describe()     use this whenever a value needs to appear in a log. It
                   reports size and a digest, never the content.

    PrivacyFilter  a logging.Filter that redacts registered private strings
                   out of any record, including ones produced by httpx,
                   sqlalchemy or uvicorn. This is the backstop for the paths
                   nobody remembered to change.

Registered values are held in memory, in this process only, so that the filter
can find them in a log line and replace them. That copy is bounded
(MAX_REGISTERED entries, oldest evicted first) and is never logged or persisted.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict

# Values registered as private, keyed by digest so registering the same value
# twice does not grow the registry. The value itself is kept, because redacting
# it means searching the log text for it, and a digest never appears in the
# message that contains the value.
_PRIVATE: "OrderedDict[str, str]" = OrderedDict()
_LOCK = threading.Lock()

# Long enough to be a fingerprint, short enough not to be a search oracle.
_DIGEST_CHARS = 12

# How many values one process remembers. Ingest registers a prompt, a fetched
# page and each of its chunks, so without a cap a long-lived worker would hold
# every page it had ever seen. Oldest first, so work in flight survives.
MAX_REGISTERED = 256


def digest(value: str) -> str:
    """A stable short fingerprint of a string. Not reversible, not a secret."""
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:_DIGEST_CHARS]


def placeholder(value: str) -> str:
    """The log-safe stand-in for a value: a digest and a length, no content."""
    return f"[private {digest(value)} {len(value)} chars]"


def register_private(value: str | None) -> str:
    """Mark a string as private and return the placeholder for it in logs.

    The value is remembered (bounded, in this process, never persisted) so that
    `PrivacyFilter` can replace it if it later reaches a log line. The
    placeholder is what a caller should log in the first place.
    """
    if not value:
        return ""
    token = digest(value)
    with _LOCK:
        _PRIVATE[token] = value
        _PRIVATE.move_to_end(token)
        while len(_PRIVATE) > MAX_REGISTERED:
            _PRIVATE.popitem(last=False)
    return placeholder(value)


def describe(value: str | None, label: str = "value") -> str:
    """A log-safe description of a value: never the value itself."""
    if value is None:
        return f"{label}=none"
    return f"{label}=[private {digest(value)} {len(value)} chars]"


def private_snapshot() -> set[str]:
    """The digests currently registered. Used by tests; not the content."""
    with _LOCK:
        return set(_PRIVATE)


def reset_private() -> None:
    """Forget every registration. Used between tests."""
    with _LOCK:
        _PRIVATE.clear()


class PrivacyFilter(logging.Filter):
    """Redact private content that reaches a log record by any route.

    Two passes. Every registered value is replaced wherever it appears, so a
    whole saved page -- or a short note -- that got formatted into a message is
    removed wholesale. Then a looser pass catches long unbroken runs of text
    that are clearly a body of content even when they were never registered --
    for example an error handler that interpolated a fetched page for the first
    time.
    """

    # Below this length a run is far more likely to be a sentence in an error
    # message than a body of private content.
    MIN_RUN_CHARS = 200

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # A malformed %s must never lose the record.
            return True

        redacted = self._redact(message)
        # Always update msg and clear args so downstream formatters (e.g. uvicorn's
        # AccessFormatter) don't fail with "not enough values to unpack".
        # The message is already formatted by getMessage() above.
        record.msg = redacted
        record.args = ()

        # The exception text is formatted separately and can carry content too.
        if record.exc_info:
            try:
                exc_text = logging.Formatter().formatException(record.exc_info)
            except Exception:
                return True
            cleaned = self._redact(exc_text)
            if cleaned != exc_text:
                record.exc_text = cleaned
                record.exc_info = None
        return True

    def _redact(self, text: str) -> str:
        if not text:
            return text
        with _LOCK:
            known = sorted(_PRIVATE.values(), key=len, reverse=True)
        # Longest first, so a value that contains another is replaced whole
        # instead of leaving a fragment of the shorter one behind.
        for value in known:
            if value in text:
                text = text.replace(value, placeholder(value))
        return self._redact_long_runs(text)

    def _redact_long_runs(self, text: str) -> str:
        """Replace unusually long unbroken prose with a marker."""
        out, run = [], []
        for char in text:
            if char.isalnum() or char in " '.,;:!?-_()[]":
                run.append(char)
                continue
            out.append(self._flush(run))
            run = []
            out.append(char)
        out.append(self._flush(run))
        return "".join(out)

    def _flush(self, run: list[str]) -> str:
        text = "".join(run)
        if len(text) >= self.MIN_RUN_CHARS and " " in text:
            return describe(text, "content")
        return text