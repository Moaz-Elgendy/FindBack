"""`GET /api/v1/snapshots/{id}`: the "Worth another look" list behind the note.

The screen this feeds has to show exactly the saves the notification counted.
Two things therefore matter and get their own tests:

  * ownership -- another user's snapshot id is a 404, never a 403, because the
    existence of somebody else's snapshot is not this route's to reveal;
  * the two counts -- a save deleted after the note was sent is dropped from
    the list, so `available_count` is what is on screen and `original_count` is
    what the lock screen claimed. Without both the user sees a shorter list
    than the note promised and no way to tell why.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.services.snapshots import create_snapshot
from test_phase16_multitenant import admin_engine, db, sessions, client, two_users, _ready_item

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)


def _forgotten_item(sessions, user_id, url, *, created=NOW - timedelta(days=30)):
    item_id, _ = _ready_item(sessions, user_id, url)
    with sessions() as s:
        s.execute(text("UPDATE items SET created_at = :c WHERE id = :i"),
                  {"c": created, "i": item_id})
        s.commit()
    return item_id


def _snapshot_id(sessions, user_id, now=NOW):
    with sessions() as s:
        snapshot = create_snapshot(s, user_id, now=now)
        return None if snapshot is None else str(snapshot.id)


# --- 1. the list -------------------------------------------------------------

def test_the_response_is_standard_memory_cards_with_both_counts(client, sessions, two_users):
    """Same shape as GET /items, so the screen renders one standard card."""
    first = _forgotten_item(sessions, two_users["a"], "https://old.test/first")
    second = _forgotten_item(sessions, two_users["a"], "https://old.test/second")
    snapshot_id = _snapshot_id(sessions, two_users["a"])

    client.as_user(two_users["a"])
    response = client.request('GET', f'/api/v1/snapshots/{snapshot_id}')
    assert response.status_code == 200, response.text
    body = response.json()

    assert {row['id'] for row in body['items']} == {first, second}
    assert body['original_count'] == 2
    assert body['available_count'] == 2
    assert body['snapshot_id'] == snapshot_id
    card = next(row for row in body['items'] if row['id'] == first)
    # The fields a memory card needs, and the ones a list never carries.
    for field in ('id', 'url', 'title', 'title_clean', 'summary', 'status',
                  'category', 'tags', 'created_at'):
        assert field in card, field
    for field in _LIST_ONLY_ON_DETAIL:
        assert field not in card, field


_LIST_ONLY_ON_DETAIL = ('transcript', 'ocr_text', 'evidence_used',
                        'processing_metadata')


def test_the_list_is_ordered_newest_save_first(client, sessions, two_users):
    """The order the notification counted in, which is newest save first."""
    oldest = _forgotten_item(sessions, two_users["a"], "https://old.test/oldest",
                             created=NOW - timedelta(days=90))
    middle = _forgotten_item(sessions, two_users["a"], "https://old.test/middle",
                             created=NOW - timedelta(days=40))
    newest = _forgotten_item(sessions, two_users["a"], "https://old.test/newest",
                             created=NOW - timedelta(days=10))
    snapshot_id = _snapshot_id(sessions, two_users["a"])

    client.as_user(two_users["a"])
    body = client.request('GET', f'/api/v1/snapshots/{snapshot_id}').json()
    assert [row['id'] for row in body['items']] == [newest, middle, oldest]


def test_it_requires_authentication(client, sessions, two_users):
    _forgotten_item(sessions, two_users["a"], "https://old.test/anon")
    snapshot_id = _snapshot_id(sessions, two_users["a"])
    client.anonymous()
    assert client.request('GET', f'/api/v1/snapshots/{snapshot_id}').status_code == 401


# --- 2. only the owner -------------------------------------------------------

def test_another_user_gets_404_for_someone_elses_snapshot(client, sessions, two_users):
    """404, not 403: the id alone must not even confirm the snapshot exists."""
    _forgotten_item(sessions, two_users["a"], "https://old.test/a-only")
    snapshot_id = _snapshot_id(sessions, two_users["a"])

    client.as_user(two_users["b"])
    denied = client.request('GET', f'/api/v1/snapshots/{snapshot_id}')
    assert denied.status_code == 404, denied.text
    assert str(two_users["a"]) not in denied.text

    # And the owner still reads their own.
    client.as_user(two_users["a"])
    assert client.request('GET', f'/api/v1/snapshots/{snapshot_id}').status_code == 200


def test_an_unknown_snapshot_id_is_404(client, two_users):
    import uuid
    client.as_user(two_users["a"])
    assert client.request('GET', f'/api/v1/snapshots/{uuid.uuid4()}').status_code == 404
    assert client.request('GET', '/api/v1/snapshots/not-a-uuid').status_code == 422


def test_a_snapshot_cannot_be_written_for_a_user_who_does_not_exist(sessions, two_users):
    """The owner scope is not only in the route: the column is a real foreign key.

    A snapshot row naming a user that is not there could never be read back --
    the route always scopes by the authenticated user -- so this is the check
    that keeps the table honest for any other writer.
    """
    import uuid

    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.models import WeeklySnapshot

    with pytest.raises(IntegrityError):
        with sessions() as s:
            s.add(WeeklySnapshot(id=uuid.uuid4(), user_id=uuid.uuid4()))
            s.flush()


# --- 3. saves deleted after the note ----------------------------------------

def test_a_permanently_deleted_save_is_still_in_the_original_count(
        client, sessions, two_users):
    kept = _forgotten_item(sessions, two_users['a'], 'https://old.test/kept-hard')
    removed = _forgotten_item(sessions, two_users['a'], 'https://old.test/removed-hard')
    snapshot_id = _snapshot_id(sessions, two_users['a'])
    client.as_user(two_users['a'])
    assert client.request('DELETE', f'/api/v1/items/{removed}').status_code == 204
    body = client.request('GET', f'/api/v1/snapshots/{snapshot_id}').json()
    assert [row['id'] for row in body['items']] == [kept]
    assert body['original_count'] == 2
    assert body['available_count'] == 1


def test_a_save_deleted_after_the_snapshot_is_omitted_but_still_counted(
        client, sessions, two_users):
    """The note said two. One was deleted afterwards. The list shows one, and
    both numbers make the difference visible instead of silently lying."""
    kept = _forgotten_item(sessions, two_users["a"], "https://old.test/kept")
    removed = _forgotten_item(sessions, two_users["a"], "https://old.test/removed")
    snapshot_id = _snapshot_id(sessions, two_users["a"])

    client.as_user(two_users["a"])
    assert client.request('DELETE', f'/api/v1/items/{removed}?undoable=true').status_code == 204

    body = client.request('GET', f'/api/v1/snapshots/{snapshot_id}').json()
    assert [row['id'] for row in body['items']] == [kept]
    assert body['original_count'] == 2, "the note's count must not shrink"
    assert body['available_count'] == 1, "only the surviving save is listed"


def test_a_restored_save_comes_back(client, sessions, two_users):
    item_id = _forgotten_item(sessions, two_users["a"], "https://old.test/undo")
    snapshot_id = _snapshot_id(sessions, two_users["a"])
    client.as_user(two_users["a"])
    client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true')
    assert client.request('GET', f'/api/v1/snapshots/{snapshot_id}').json()['items'] == []

    client.request('POST', f'/api/v1/items/{item_id}/restore')
    body = client.request('GET', f'/api/v1/snapshots/{snapshot_id}').json()
    assert [row['id'] for row in body['items']] == [item_id]
    assert body['available_count'] == 1


# --- 4. no leakage -----------------------------------------------------------

def test_the_list_never_contains_another_users_saves(client, sessions, two_users):
    """Both users have snapshots; neither may see the other's save."""
    _forgotten_item(sessions, two_users["a"], "https://old.test/only-a")
    _forgotten_item(sessions, two_users["b"], "https://old.test/only-b")
    a_snapshot = _snapshot_id(sessions, two_users["a"])
    b_snapshot = _snapshot_id(sessions, two_users["b"])

    client.as_user(two_users["a"])
    body = client.request('GET', f'/api/v1/snapshots/{a_snapshot}')
    assert two_users["b_item"] not in body.text
    client.as_user(two_users["b"])
    body = client.request('GET', f'/api/v1/snapshots/{b_snapshot}')
    assert two_users["a_item"] not in body.text


def test_a_snapshot_never_contains_a_save_that_was_not_forgotten(sessions, two_users):
    """The list is the frozen forgotten set, not the user's whole library."""
    forgotten = _forgotten_item(sessions, two_users["a"], "https://old.test/forgotten")
    _ready_item(sessions, two_users["a"], "https://fresh.test/recent")
    snapshot_id = _snapshot_id(sessions, two_users["a"])
    with sessions() as s:
        from app.models import SnapshotItem
        stored = [str(row.save_id) for row in s.query(SnapshotItem)
                  .filter(SnapshotItem.snapshot_id == snapshot_id).all()]
    assert stored == [forgotten]
