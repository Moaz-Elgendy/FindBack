"""Tests for logging filters: access log formatting and redaction behavior."""

import json
import logging
import sys
from logging import LogRecord

import pytest

from app.main import _install_secret_masking, _install_privacy_filtering, SecretMask
from app.services import privacy


def _make_record(msg: str, args: tuple = (), name: str = "test") -> LogRecord:
    """Create a LogRecord with the given message and args."""
    return LogRecord(
        name=name,
        level=logging.INFO,
        pathname="",
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


class TestSecretMaskFilter:
    """Tests for the SecretMask filter."""

    def test_access_log_record_formats_without_error(self):
        """
        uvicorn access log records must format without ValueError.

        The original bug: SecretMask.filter() set record.args = () BEFORE formatting,
        which broke uvicorn's AccessFormatter that expects (client, request_line, status_code).
        The fix: format the message first (consuming args), then clear args.
        """
        from app.main import SecretMask

        mask = SecretMask()

        # uvicorn access log format: '%(client)s - "%(request_line)s" %(status_code)s'
        # args: (client_addr, request_line, status_code)
        record = _make_record(
            '%s - "%s" %s',
            args=("127.0.0.1:12345", "GET /health HTTP/1.1", "200"),
            name="uvicorn.access",
        )

        # This must not raise
        result = mask.filter(record)

        # Filter returns True (keep record)
        assert result is True
        # Message is now pre-formatted, args consumed
        assert record.msg == '127.0.0.1:12345 - "GET /health HTTP/1.1" 200'
        assert record.args == ()
        # getMessage() must work without ValueError
        formatted = record.getMessage()
        assert formatted == '127.0.0.1:12345 - "GET /health HTTP/1.1" 200'

    def test_secret_redaction_works_on_other_loggers(self):
        """Secrets in messages are still redacted on non-access loggers."""
        from app.main import SecretMask
        from app import env

        # Set a fake secret in the environment for the test
        import os
        os.environ["GROQ_API_KEY"] = "gsk_testsecret1234567890"

        try:
            mask = SecretMask()
            record = _make_record(
                "Calling API with key gsk_testsecret1234567890",
                name="findback.api",
            )

            result = mask.filter(record)
            assert result is True
            # The secret should be masked (mask_key format: first 5 + ... + last 2 + (len chars))
            assert "gsk_testsecret1234567890" not in record.msg
            assert "gsk_t" in record.msg and "...90" in record.msg and "(24 chars)" in record.msg
            # getMessage should work
            formatted = record.getMessage()
            assert "gsk_testsecret1234567890" not in formatted
        finally:
            del os.environ["GROQ_API_KEY"]

    def test_regression_no_valueerror_on_clear_args(self):
        """
        Regression test: ensure we don't get 'ValueError: not enough values to unpack'
        when formatting records that had args cleared.
        """
        from app.main import SecretMask

        mask = SecretMask()

        # Simulate what uvicorn access formatter expects
        record = _make_record(
            '%s - "%s" %s',
            args=("127.0.0.1:12345", "GET /health HTTP/1.1", "200"),
        )

        # Apply filter
        mask.filter(record)

        # Now try to format the record (what uvicorn does)
        # This used to raise: ValueError: not enough values to unpack (expected 5, got 0)
        formatted = record.getMessage()
        assert formatted == '127.0.0.1:12345 - "GET /health HTTP/1.1" 200'


class TestPrivacyFilter:
    """Tests for the PrivacyFilter."""

    def setup_method(self):
        privacy.reset_private()

    def test_access_log_record_formats_without_error(self):
        """
        uvicorn access log records must format without ValueError.

        PrivacyFilter.filter() also set record.args = () before formatting, which broke uvicorn.
        The fix: format the message first (consuming args), then clear args.
        """
        filt = privacy.PrivacyFilter()

        record = _make_record(
            '%s - "%s" %s',
            args=("127.0.0.1:12345", "GET /health HTTP/1.1", "200"),
            name="uvicorn.access",
        )

        result = filt.filter(record)
        assert result is True
        # Message is now pre-formatted, args consumed
        assert record.msg == '127.0.0.1:12345 - "GET /health HTTP/1.1" 200'
        assert record.args == ()
        # getMessage() must work without ValueError
        formatted = record.getMessage()
        assert formatted == '127.0.0.1:12345 - "GET /health HTTP/1.1" 200'

    def test_private_content_redaction_works(self):
        """Private content registered via register_private is redacted.

        The VALUE is logged here, not the placeholder `register_private`
        returns. Most call sites never see that placeholder -- an httpx error or
        a traceback formats the value itself -- so the filter has to find and
        replace the registered value. The old filter searched for the value's
        digest, which never appears in a log line, so this test failed on it.
        """
        secret = "this is a private string that must be redacted"
        privacy.register_private(secret)

        filt = privacy.PrivacyFilter()
        record = _make_record(f"User saved: {secret}", name="findback.api")

        result = filt.filter(record)
        assert result is True
        assert secret not in record.msg
        assert "[private" in record.msg
        formatted = record.getMessage()
        assert secret not in formatted
        assert "[private" in formatted

    def test_a_short_registered_value_is_redacted(self):
        """Short values are the case the 200-char heuristic cannot cover."""
        secret = "MY-PRIVATE-NOTE-abcdef"
        privacy.register_private(secret)

        record = _make_record(f"user note: {secret}", name="findback.api")
        assert privacy.PrivacyFilter().filter(record) is True
        assert secret not in record.getMessage()
        assert "[private" in record.msg

    def test_a_registered_value_inside_json_is_redacted(self):
        """A model answer is JSON; its punctuation defeats the run heuristic."""
        secret = "Risotto for mum, the good one"
        privacy.register_private(secret)
        payload = json.dumps({"title": secret, "highlights": ["use double cream"]})

        record = _make_record("model answer: %s", args=(payload,),
                              name="findback.extractor")
        assert privacy.PrivacyFilter().filter(record) is True
        formatted = record.getMessage()
        assert secret not in formatted
        assert "use double cream" in formatted, "the rest of the record must survive"

    def test_a_registered_value_inside_an_exception_message_is_redacted(self):
        """Tracebacks are formatted separately and carry content too."""
        secret = "the risotto recipe for mum"
        privacy.register_private(secret)

        record = _make_record("ingest failed", name="findback.api")
        try:
            raise RuntimeError(f"failed while saving {secret}")
        except RuntimeError:
            record.exc_info = sys.exc_info()

        assert privacy.PrivacyFilter().filter(record) is True
        assert record.exc_text is not None, "the exception text was not rewritten"
        assert secret not in record.exc_text
        assert "failed while saving" in record.exc_text

    def test_the_registry_is_bounded(self):
        """The registry must not grow without limit over a long-lived process."""
        for index in range(privacy.MAX_REGISTERED + 50):
            privacy.register_private(f"private-value-{index}")

        assert len(privacy.private_snapshot()) <= privacy.MAX_REGISTERED
        newest = f"private-value-{privacy.MAX_REGISTERED + 49}"
        record = _make_record(f"note: {newest}", name="findback.api")
        assert privacy.PrivacyFilter().filter(record) is True
        assert newest not in record.getMessage()

    def test_long_unregistered_content_redaction(self):
        """Long unbroken runs of text are redacted even if not registered."""
        filt = privacy.PrivacyFilter()
        long_text = "x " * 150  # > 200 chars with spaces

        record = _make_record(f"Error processing: {long_text}", name="findback.api")
        result = filt.filter(record)
        assert result is True
        assert "[private" in record.msg or "content=" in record.msg
        # getMessage should work
        formatted = record.getMessage()
        assert "[private" in formatted or "content=" in formatted

    def test_regression_no_valueerror_on_clear_args(self):
        """Regression test for the original ValueError."""
        filt = privacy.PrivacyFilter()

        record = _make_record(
            '%s - "%s" %s',
            args=("127.0.0.1:12345", "GET /health HTTP/1.1", "200"),
        )

        filt.filter(record)

        formatted = record.getMessage()
        assert formatted == '127.0.0.1:12345 - "GET /health HTTP/1.1" 200'


class TestFilterInstallation:
    """Tests that filters are installed on correct loggers."""

    def test_secret_mask_not_installed_on_uvicorn_access(self):
        """SecretMask should not be installed on uvicorn.access logger."""
        # Clear any existing handlers/filters
        for name in ("", "findback", "uvicorn", "uvicorn.error", "uvicorn.access",
                     "sqlalchemy.engine"):
            logger = logging.getLogger(name)
            logger.handlers.clear()

        # Add handlers to both uvicorn.access and root
        access_logger = logging.getLogger("uvicorn.access")
        access_handler = logging.StreamHandler()
        access_logger.addHandler(access_handler)

        root_logger = logging.getLogger("")
        root_handler = logging.StreamHandler()
        root_logger.addHandler(root_handler)

        _install_secret_masking()

        # uvicorn.access should NOT have the filter
        secret_mask_filters = [f for f in access_handler.filters
                               if isinstance(f, SecretMask)]
        assert len(secret_mask_filters) == 0

        # But root logger should have it
        secret_mask_filters = [f for f in root_handler.filters
                               if isinstance(f, SecretMask)]
        assert len(secret_mask_filters) == 1

    def test_privacy_filter_not_installed_on_uvicorn_access(self):
        """PrivacyFilter should not be installed on uvicorn.access logger."""
        for name in ("", "findback", "uvicorn", "uvicorn.error", "uvicorn.access",
                     "sqlalchemy.engine", "httpx", "httpcore"):
            logger = logging.getLogger(name)
            logger.handlers.clear()

        # Add handlers to both uvicorn.access and root
        access_logger = logging.getLogger("uvicorn.access")
        access_handler = logging.StreamHandler()
        access_logger.addHandler(access_handler)

        root_logger = logging.getLogger("")
        root_handler = logging.StreamHandler()
        root_logger.addHandler(root_handler)

        _install_privacy_filtering()

        # uvicorn.access should NOT have the filter
        privacy_filters = [f for f in access_handler.filters
                           if isinstance(f, privacy.PrivacyFilter)]
        assert len(privacy_filters) == 0

        # But root logger should have it
        privacy_filters = [f for f in root_handler.filters
                           if isinstance(f, privacy.PrivacyFilter)]
        assert len(privacy_filters) == 1

    def test_the_celery_worker_installs_the_log_filters(self):
        """A worker process never imports app.main, so it installs them itself.

        Fetching, the AI call and embedding all run in the worker, which is
        where the raw content is handled. Without this hook none of that logging
        is filtered.
        """
        import io

        from celery.signals import worker_process_init

        from app import celery_app  # noqa: F401 - registers the worker hook

        logger = logging.getLogger("findback")
        logger.handlers.clear()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        try:
            worker_process_init.send(sender=None)
            assert any(isinstance(f, privacy.PrivacyFilter)
                       for f in handler.filters), \
                "the worker process did not install the privacy filter"
            assert any(f.__class__.__name__ == "SecretMask"
                       for f in handler.filters), \
                "the worker process did not install the secret mask"
        finally:
            logger.removeHandler(handler)


class TestInstallIdempotent:
    """Installing filters twice should not duplicate them."""

    def setup_method(self):
        for name in ("", "findback", "uvicorn", "uvicorn.error", "uvicorn.access",
                     "sqlalchemy.engine", "httpx", "httpcore"):
            logger = logging.getLogger(name)
            logger.handlers.clear()

    def test_secret_mask_idempotent(self):
        logger = logging.getLogger("findback")
        handler = logging.StreamHandler()
        logger.addHandler(handler)

        _install_secret_masking()
        _install_secret_masking()

        secret_mask_filters = [f for f in handler.filters
                               if f.__class__.__name__ == "SecretMask"]
        assert len(secret_mask_filters) == 1

    def test_privacy_filter_idempotent(self):
        logger = logging.getLogger("findback")
        handler = logging.StreamHandler()
        logger.addHandler(handler)

        _install_privacy_filtering()
        _install_privacy_filtering()

        privacy_filters = [f for f in handler.filters
                           if f.__class__.__name__ == "PrivacyFilter"]
        assert len(privacy_filters) == 1


class TestNothingBypassesTheFilters:
    """A print() writes straight to stdout, past every handler filter."""

    def test_a_failed_search_query_is_logged_not_printed(self, caplog, capsys):
        import uuid

        from app.services import search

        class BoomDb:
            def execute(self, *args, **kwargs):
                raise RuntimeError("vector outage")

        with caplog.at_level(logging.WARNING):
            rows = search.lexical_search(BoomDb(), uuid.uuid4(), "risotto",
                                         ["risotto"])

        assert rows == []
        assert "lexical failed" in caplog.text, "the failure was not logged"
        assert capsys.readouterr().out == "", "the failure was printed"

    def test_the_search_and_task_modules_do_not_print(self):
        import pathlib

        backend = pathlib.Path(__file__).resolve().parents[1]
        for relative in ("app/services/search.py", "app/tasks.py"):
            source = (backend / relative).read_text(encoding="utf-8")
            offenders = [line.strip() for line in source.splitlines()
                         if line.lstrip().startswith("print(")]
            assert offenders == [], f"{relative} still prints: {offenders}"
