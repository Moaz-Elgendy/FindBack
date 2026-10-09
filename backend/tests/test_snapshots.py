"""`create_snapshot` freezes the forgotten set, and writes nothing when it is empty.

The empty case is the interesting one: the addendum says a zero count sends no
notification, so an empty snapshot row would be a note with no list behind it.
It must not be created at all.

Times are pinned through `now`, exactly as in test_forgotten.py, so the
seven-day boundary is asserted rather than approximated.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.models import SnapshotItem, WeeklySnapshot
from app.services.forgotten import FORGOTTEN_AFTER_DAYS
from app.services.snapshots import create_snapshot
from test_phase16_multitenant import admin_engine, db, sessions, two_users, _ready_item

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)


def _stamp(sessions, item_id, *, created, opened=None, deleted=None):
    with sessions() as s:
        s.execute(text(
            "UPDATE items SET created_at = :c, first_opened_at = :o, "
            "deleted_at = :d WHERE id = :i"),
            {"c": created, "o": opened, "d": deleted, "i": item_id})
        s.commit()


def _forgotten_item(sessions, user_id, url, *, created=NOW - timedelta(days=30)):
    """A save that qualifies as forgotten, and its id."""
    item_id, _ = _ready_item(sessions, user_id, url)
    _stamp(sessions, item_id, created=created)
    return item_id


def _snapshot_id(sessions, user_id, now=NOW):
    """Create a snapshot and return its id.

    The id is read back inside the session: `create_snapshot` commits, which
    expires every attribute, so the returned ORM object cannot be touched once
    the session closes.
    """
    with sessions() as s:
        snapshot = create_snapshot(s, user_id, now=now)
        return None if snapshot is None else str(snapshot.id)


def _stored_ids(sessions, snapshot_id):
    with sessions() as s:
        return [str(row.save_id) for row in s.query(SnapshotItem)
                .filter(SnapshotItem.snapshot_id == snapshot_id).all()]


# --- 1. nothing forgotten -> no snapshot, no rows --------------------------

def test_nothing_forgotten_creates_nothing_and_returns_none(sessions, two_users):
    """The fixture saves were just created, so neither is forgotten."""
    assert _snapshot_id(sessions, two_users["a"]) is None
    with sessions() as s:
        assert s.query(WeeklySnapshot).count() == 0
        assert s.query(SnapshotItem).count() == 0


def test_an_opened_save_leaves_the_snapshot_uncreated(sessions, two_users):
    """Opened saves are not forgotten, so they do not become a snapshot."""
    item_id = _forgotten_item(sessions, two_users["a"], "https://old.test/opened")
    _stamp(sessions, item_id, created=NOW - timedelta(days=30),
           opened=NOW - timedelta(days=20))
    assert _snapshot_id(sessions, two_users["a"]) is None


def test_a_deleted_save_leaves_the_snapshot_uncreated(sessions, two_users):
    item_id = _forgotten_item(sessions, two_users["a"], "https://old.test/gone")
    _stamp(sessions, item_id, created=NOW - timedelta(days=30),
           deleted=NOW - timedelta(days=1))
    assert _snapshot_id(sessions, two_users["a"]) is None


# --- 2. the stored ids ------------------------------------------------------

def test_the_snapshot_stores_exactly_the_forgotten_ids(sessions, two_users):
    older = _forgotten_item(sessions, two_users["a"], "https://old.test/older")
    newest = _forgotten_item(sessions, two_users["a"], "https://old.test/newest")

    snapshot_id = _snapshot_id(sessions, two_users["a"])
    assert snapshot_id is not None
    # As a set: `snapshot_items` is keyed (snapshot_id, save_id) with no
    # position column, so the display order is applied where the list is read,
    # not stored here. Ordering is asserted in tests/test_snapshot_read.py.
    assert set(_stored_ids(sessions, snapshot_id)) == {newest, older}


def test_a_snapshot_records_its_owner_and_a_creation_time(sessions, two_users):
    _forgotten_item(sessions, two_users["a"], "https://old.test/owned")
    snapshot_id = _snapshot_id(sessions, two_users["a"])
    with sessions() as s:
        stored = s.get(WeeklySnapshot, snapshot_id)
        assert str(stored.user_id) == str(two_users["a"])
        assert stored.created_at is not None


# --- 3. isolation -----------------------------------------------------------

def test_a_snapshot_holds_only_its_own_users_saves(sessions, two_users):
    """Both users qualify; neither may see the other's id."""
    a_item = _forgotten_item(sessions, two_users["a"], "https://old.test/a")
    _forgotten_item(sessions, two_users["b"], "https://old.test/b")

    a_snapshot = _snapshot_id(sessions, two_users["a"])
    stored = _stored_ids(sessions, a_snapshot)
    assert stored == [a_item]
    assert two_users["b_item"] not in stored


# --- 4. the seven-day boundary ---------------------------------------------

def test_a_save_exactly_on_the_boundary_is_not_counted(sessions, two_users):
    _forgotten_item(sessions, two_users["a"], "https://old.test/boundary",
                    created=NOW - timedelta(days=FORGOTTEN_AFTER_DAYS))
    assert _snapshot_id(sessions, two_users["a"]) is None


def test_a_save_one_microsecond_past_the_boundary_is_counted(sessions, two_users):
    item_id = _forgotten_item(
        sessions, two_users["a"], "https://old.test/past",
        created=NOW - timedelta(days=FORGOTTEN_AFTER_DAYS, microseconds=1))
    snapshot_id = _snapshot_id(sessions, two_users["a"])
    assert _stored_ids(sessions, snapshot_id) == [item_id]


# --- 5. repeated calls are separate snapshots -------------------------------

def test_two_calls_make_two_independent_snapshots(sessions, two_users):
    """create_snapshot is not idempotent by design: the once-per-week guard
    lives in the caller that sends the note, not here."""
    _forgotten_item(sessions, two_users["a"], "https://old.test/one")
    first = _snapshot_id(sessions, two_users["a"])
    second = _snapshot_id(sessions, two_users["a"])
    assert first != second
    assert _stored_ids(sessions, second) == _stored_ids(sessions, first)
def test_snapshot_tables_are_private(db):
    from sqlalchemy import text
    with db.connect() as connection:
        rows = connection.execute(text(
            "SELECT relname, relrowsecurity FROM pg_class "
            "WHERE relname IN ('weekly_snapshots', 'snapshot_items')")).all()
    assert dict(rows) == {'weekly_snapshots': True, 'snapshot_items': True}
