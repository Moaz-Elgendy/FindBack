"""Structured log events with a fixed field vocabulary (Phase 18).

A log line is only useful if you can filter it, and you cannot filter a message
that was assembled by string formatting at nine different call sites. So an
event is emitted here, as JSON, from an allowlist of fields.

    {"event": "ingest.completed", "request_id": "...", "user_id": "...",
     "content_id": "...", "status": "created", "duration_ms": 41}

Why the fields are an allowlist, and not arguments
---------------------------------------------------
The privacy rule for this project is that no raw private content reaches a log.
A saved page, a transcript, a note, a search query -- all of it is the user's
content (`app/services/privacy.py` is the backstop for whatever slips through).

An allowlist makes that structural rather than a matter of care. `log_event`
accepts only these fields, and each is validated:

    request_id         str, opaque, from the caller or generated
    user_id            identifier, hashed, never an address or a name
    content_id         identifier, hashed
    job_id             identifier, hashed
    pipeline_version   short slug, e.g. "process_item:v1"
    stage              short slug, e.g. "FETCH"
    status             short slug, e.g. "created"
    duration_ms        a number

Identifiers are hashed on the way in. They still correlate every line of one
request without ever being a lookup key into a user table from a log index, and
the raw value cannot be recovered from them. Anything that is not one of these
fields is a TypeError, so passing a note or a URL is a loud failure rather than
a leak that only a careful reviewer would catch.

`app/services/privacy.py`'s PrivacyFilter still runs over these records as a
second line of defence, installed on every handler by `main.py`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from contextvars import ContextVar

log = logging.getLogger("findback.observability")

REQUEST_ID_HEADER = "X-Request-ID"

# The complete vocabulary. Anything else is refused.
ALLOWED_FIELDS = frozenset({
    "request_id", "user_id", "content_id", "job_id",
    "pipeline_version", "stage", "status", "duration_ms",
})

# Fields that identify something and are therefore hashed before they are
# written. `request_id` is deliberately absent: it is ours, not the user's, and
# an operator has to be able to read it back to find a request.
_HASHED_FIELDS = frozenset({"user_id", "content_id", "job_id"})

# Fields that are slugs from a closed vocabulary. Free text here would be a way
# to smuggle content into a log, so the shape is enforced.
_SLUG_FIELDS = frozenset({"pipeline_version", "stage", "status"})

_SLUG_MAX = 64
_ID_MAX = 128

_current_request_id: ContextVar[str | None] = ContextVar(
    "findback_request_id", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex


def current_request_id() -> str | None:
    return _current_request_id.get()


def bind_request_id(request_id: str) -> object:
    """Make this id the current one. Returns a token for `reset_request_id`."""
    return _current_request_id.set(request_id)


def reset_request_id(token) -> None:
    _current_request_id.reset(token)


def digest_id(value) -> str | None:
    """A short, stable stand-in for an identifier.

    Same length as `privacy.digest` so the two read alike, and deliberately
    NOT reversible. A log index can therefore join lines of one request
    together without holding anything that identifies a person.
    """
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


# The characters a slug may contain. Braces are here because `stage` also
# carries a route template such as `/api/v1/items/{item_id}`, which is the one
# form with a path parameter in it. None of these can start a sentence, which is
# the property that matters: free text is what would put content in a log.
_SLUG_CHARS = frozenset("._:/-{}")


def _check_slug(name: str, value):
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string, got {type(value).__name__}")
    text = value.strip()
    if len(text) > _SLUG_MAX:
        raise ValueError(f"{name} is longer than {_SLUG_MAX} characters")
    if not text:
        raise ValueError(f"{name} is empty")
    for char in text:
        if not (char.isalnum() or char in _SLUG_CHARS):
            raise ValueError(
                f"{name} must be a slug of letters, digits and "
                f"{''.join(sorted(_SLUG_CHARS))}; got {value!r}. Free text "
                f"does not belong in a log field.")
    # `/` is allowed only for a route TEMPLATE, which always carries a path
    # parameter. Without this the charset alone would accept a URL --
    # "https://example.test/a/page" is letters, dots, slashes and a colon --
    # and a saved page's address is exactly the kind of thing that must not be
    # able to sit in a `status` field.
    if "/" in text and not _looks_like_route(text):
        raise ValueError(
            f"{name} may only contain a slash as a route template such as "
            f"/api/v1/items/{{item_id}}; got {value!r}")
    return text


def _looks_like_route(text: str) -> bool:
    return (text.startswith("/")
            and "{" in text and "}" in text
            and "//" not in text and "?" not in text)


def _check_id(name: str, value) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string, got {type(value).__name__}")
    if len(value) > _ID_MAX:
        raise ValueError(f"{name} is longer than {_ID_MAX} characters")
    return value


def safe_route_template(path) -> str:
    """A route template that is safe to log, or "unmatched".

    Never the raw request path: `/api/v1/items/<uuid>` would put an identifier
    in every access log, and a search query in the query string would put the
    user's content there.
    """
    if not isinstance(path, str) or not path:
        return "unmatched"
    try:
        return _check_slug("stage", path)
    except (TypeError, ValueError):
        return "unmatched"


def validate_request_id(value) -> str:
    """An inbound request id, or a fresh one if it is unusable.

    A caller may supply `X-Request-ID` so their own trace lines join up with
    ours. It is still validated, because it is attacker-controlled: a value
    full of newlines would let a caller forge extra log lines.
    """
    if not isinstance(value, str):
        return new_request_id()
    cleaned = value.strip()
    if not cleaned or len(cleaned) > _ID_MAX:
        return new_request_id()
    for char in cleaned:
        if not (char.isalnum() or char in "-_:./"):
            return new_request_id()
    return cleaned


def prepare(event: str, fields: dict) -> dict:
    """Validate and normalise the fields for one event.

    Split out from `log_event` so a test can assert on the payload without
    capturing a log record, and so the same shape is available to anything that
    wants to ship an event somewhere other than a log.
    """
    if not event or "/" in event or " " in event:
        raise ValueError(f"event must be a dotted slug, got {event!r}")
    unknown = set(fields) - ALLOWED_FIELDS
    if unknown:
        raise TypeError(
            f"unknown log field(s) {sorted(unknown)}; allowed: "
            f"{sorted(ALLOWED_FIELDS)}. Passing anything else is how private "
            f"content reaches a log.")
    if "request_id" not in fields:
        inherited = current_request_id()
        if inherited:
            fields = {**fields, "request_id": inherited}

    payload = {"event": event}
    for name, value in fields.items():
        if value is None:
            continue
        if name in _HASHED_FIELDS:
            payload[name] = digest_id(value)
        elif name in _SLUG_FIELDS:
            payload[name] = _check_slug(name, value)
        elif name == "duration_ms":
            payload[name] = int(round(float(value)))
        else:
            payload[name] = _check_id(name, value)
    return payload


def log_event(event: str, level: int = logging.INFO, **fields) -> dict:
    """Emit one structured event. Returns the payload it wrote."""
    payload = prepare(event, fields)
    log.log(level, json.dumps(payload, sort_keys=True, default=str))
    return payload


def describe_exc(exc: BaseException) -> str:
    """A log-safe description of an exception.

    The exception TYPE is safe and useful. Its message frequently is not: a
    provider error quotes the request that failed, which carries the user's
    content. So only the type and the length of the message are reported, which
    is the same rule `privacy.describe` applies to a value.
    """
    message = str(exc)
    if not message:
        return type(exc).__name__
    return f"{type(exc).__name__}({len(message)} chars)"
