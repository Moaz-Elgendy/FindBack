"""Log filters, installed in every process that handles user content (Phase 14).

Both filters belong to every long-running process, not only the API:

    SecretMask     redacts configured API keys from any record
    PrivacyFilter  redacts registered private content (app.services.privacy)

The API installs them from its lifespan. A Celery worker never imports
`app.main`, so `app/celery_app.py` installs them on `worker_process_init` -- and
that is the process that fetches pages, calls the model and embeds text, so it
is the one that matters most.

`uvicorn.access` is deliberately excluded from both: its records have a fixed
format string that expects specific args, and clearing args breaks the
formatter. Access logs preserve their arguments; share paths have a dedicated mask.
"""
from __future__ import annotations

import logging
import re

from app import env
from app.services import privacy


SHARE_PATH = re.compile(r'(/(?:s|api/v1/shares)/)[^/?#\s]+')


class ShareAccessMask(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            args[2] = SHARE_PATH.sub(r'\1<share-token>', str(args[2]))
            record.args = tuple(args)
        return True


class SecretMask(logging.Filter):
    """Redact configured API keys from every record that passes through.

    Provider and httpx errors quote the request they failed, and the key sits in
    the Authorization header of that request. Filtering at the handler catches
    records from uvicorn and sqlalchemy too, not just our own loggers.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text_ = record.getMessage()
        except Exception:  # a malformed % formatting must not lose the record
            return True
        for name in env.SECRET_ENV_KEYS:
            secret = env.get(name)
            if secret and len(secret) > 5 and secret in text_:
                text_ = text_.replace(secret, env.mask_key(secret))
        text_ = SHARE_PATH.sub(r'\1<share-token>', text_)
        record.msg = text_
        record.args = ()
        # Note: we clear args after formatting the message so that downstream
        # formatters (like uvicorn's AccessFormatter) don't fail with
        # "not enough values to unpack". The message is already formatted.
        return True


def install_secret_masking() -> None:
    """Attach one SecretMask to every handler that could print a secret.

    uvicorn configures its own loggers with propagate=False, so filtering the
    root logger alone would miss exactly the access/error logs we care about.
    uvicorn.access is intentionally excluded: its records have a fixed format
    string that expects specific args; clearing args breaks the formatter.
    Share paths are masked separately without clearing access arguments.
    """
    mask = SecretMask()
    for name in ("", "findback", "uvicorn", "uvicorn.error",
                 "sqlalchemy.engine"):
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            # Check by class to be idempotent across multiple calls
            if not any(isinstance(f, SecretMask) for f in handler.filters):
                handler.addFilter(mask)


def install_privacy_filtering() -> None:
    """Redact private content from every handler, not just our own loggers.

    Same reasoning as `install_secret_masking`: a saved page must not reach a
    log through httpx, sqlalchemy or uvicorn either.
    uvicorn.access is intentionally excluded: its records have a fixed format
    string that expects specific args; clearing args breaks the formatter.
    Access logs do not carry private content.
    """
    filt = privacy.PrivacyFilter()
    for name in ("", "findback", "uvicorn", "uvicorn.error",
                 "sqlalchemy.engine", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            if not any(isinstance(f, privacy.PrivacyFilter) for f in handler.filters):
                handler.addFilter(filt)


def install_log_filters() -> None:
    """Install both filters. Idempotent, so any process may call it any time."""
    install_secret_masking()
    install_privacy_filtering()
    access = logging.getLogger('uvicorn.access')
    if not any(isinstance(f, ShareAccessMask) for f in access.filters):
        access.addFilter(ShareAccessMask())
