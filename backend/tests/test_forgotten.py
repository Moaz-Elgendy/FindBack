"""The forgotten-save definition: old, never opened, not deleted, one user.

Every case the definition turns on gets its own test, because each one is a
line of the WHERE clause: ownership, the 7-day boundary, `deleted_at`, and
`first_opened_at`. Time is pinned through the function's `now` argument so the
boundary assertions are exact instead of "roughly seven days".
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.services.forgotten import forgotten_save_ids
from test_phase16_multitenant import admin_engine, db, sessions, two_users, _ready_item

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
SEVEN_DAYS = timedelta(days=7)


def _stamp(sessions, item_id, *, created, opened=None, deleted=None):
    """Pin a save's timestamps so the cutoffs are exact, not approximate."""
    with sessions() as s:
        s.execute(text(
            "UPDATE items SET created_at = :c, first_opened_at = :o, "
            "deleted_at = :d WHERE id = :i"),
            {"c": created, "o": opened, "d": deleted, "i": item_id})
        s.commit()


def _forgotten(sessions, user_id, now=NOW):
    with sessions() as s:
        return forgotten_save_ids(s, user_id, now=now)


# --- 1. none found -> empty list ------------------------------------------

def test_nothing_old_yet_returns_an_empty_list(sessions, two_users):
    """Both fixture saves were just created, so neither is forgotten."""
    assert _forgotten(sessions, two_users["a"]) == []
    assert _forgotten(sessions, two_users["b"]) == []


# --- 2. ordered newest first -----------------------------------------------

def test_ids_come_back_newest_save_first(sessions, two_users):
    older, _ = _ready_item(sessions, two_users["a"], "https://old.test/oldest")
    middle, _ = _ready_item(sessions, two_users["a"], "https://old.test/middle")
    newest, _ = _ready_item(sessions, two_users["a"], "https://old.test/newest")
    _stamp(sessions, older, created=NOW - timedelta(days=40))
    _stamp(sessions, middle, created=NOW - timedelta(days=10))
    _stamp(sessions, newest, created=NOW - timedelta(days=8))

    ids = _forgotten(sessions, two_users["a"])
    assert ids == [newest, middle, older], (
        "forgotten ids must be ordered newest save first")


# --- 3. scoped to one user --------------------------------------------------

def test_another_users_saves_are_excluded(sessions, two_users):
    """Both users qualify; each must see only their own id."""
    _stamp(sessions, two_users["a_item"], created=NOW - timedelta(days=30))
    _stamp(sessions, two_users["b_item"], created=NOW - timedelta(days=30))

    a_ids = _forgotten(sessions, two_users["a"])
    b_ids = _forgotten(sessions, two_users["b"])
    assert a_ids == [two_users["a_item"]], "B's save leaked into A's result"
    assert b_ids == [two_users["b_item"]], "A's save leaked into B's result"


# --- 4. deleted saves -------------------------------------------------------

def test_deleted_saves_are_excluded(sessions, two_users):
    _stamp(sessions, two_users["a_item"],
           created=NOW - timedelta(days=30), deleted=NOW - timedelta(days=1))
    assert _forgotten(sessions, two_users["a"]) == []


# --- 5. the seven-day boundary ---------------------------------------------

def test_exactly_seven_days_old_is_not_forgotten_yet(sessions, two_users):
    """More than seven days, not seven days: the boundary itself is excluded."""
    _stamp(sessions, two_users["a_item"], created=NOW - SEVEN_DAYS)
    assert _forgotten(sessions, two_users["a"]) == []


def test_one_microsecond_past_seven_days_is_forgotten(sessions, two_users):
    _stamp(sessions, two_users["a_item"],
           created=NOW - SEVEN_DAYS - timedelta(microseconds=1))
    assert _forgotten(sessions, two_users["a"]) == [two_users["a_item"]]


# --- 6. opened saves --------------------------------------------------------

def test_an_opened_save_is_never_forgotten(sessions, two_users):
    _stamp(sessions, two_users["a_item"],
           created=NOW - timedelta(days=30),
           opened=NOW - timedelta(days=20))
    assert _forgotten(sessions, two_users["a"]) == []


# --- 7. older saves, never touched, still count -----------------------------

def test_a_save_older_than_the_column_is_forgotten_without_a_backfill(
        sessions, two_users):
    """No special backfill: created long ago and never opened is exactly NULL."""
    _stamp(sessions, two_users["a_item"], created=NOW - timedelta(days=400))
    assert _forgotten(sessions, two_users["a"]) == [two_users["a_item"]]
