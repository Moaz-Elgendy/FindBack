"""Regression tests for the ingest title/preview helpers.

`derive_title` used to be an inline expression whose conditional bound before
the `or`, so a Share Sheet payload carrying only a title_hint stored title=None.
"""
from app.utils.text import derive_title, truncate


def test_title_hint_wins_when_no_preview_is_sent():
    # The original bug: `(hint or preview[:120]) if preview else None` -> None.
    assert derive_title("Grandma's Soufflé", None) == "Grandma's Soufflé"


def test_title_hint_beats_preview():
    assert derive_title("Oven Temp Guide", "a long preview body") == "Oven Temp Guide"


def test_falls_back_to_preview_head():
    assert derive_title(None, "x" * 200) == "x" * 120


def test_whitespace_only_hint_falls_back_to_preview():
    assert derive_title("   ", "preview text here") == "preview text here"


def test_hint_is_clipped():
    assert derive_title("t" * 400, None) == "t" * 120


def test_no_inputs_yields_none():
    assert derive_title(None, None) is None
    assert derive_title("", "") is None


def test_truncate_passthrough():
    assert truncate(None, 10) is None
    assert truncate("", 10) is None
    assert truncate("abc", 10) == "abc"
    assert truncate("abcdef", 3) == "abc"
