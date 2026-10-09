"""The weekly toggle and schedule the settings screen writes are the ones the
beat task obeys.

The settings screen PUTs `/api/v1/account/weekly-note`; the task reads
`weekly_note_preferences` every tick. Nothing connects the two except that
table, so these tests drive the real endpoint and then run the real tick --
turning the switch off and changing the day, time or zone must each change what
the tick does on its very next run.
"""
import datetime

from sqlalchemy import text

from test_phase16_multitenant import admin_engine, db, sessions, client, two_users, _ready_item

PATH = '/api/v1/account/weekly-note'
NOW = datetime.datetime(2026, 10, 8, 12, 0, 0, tzinfo=datetime.timezone.utc)
THURSDAY_18_00 = datetime.datetime(2026, 10, 8, 18, 0, tzinfo=datetime.timezone.utc)


def _forgotten(sessions, user_id, url='https://old.test/a'):
    item_id, _ = _ready_item(sessions, user_id, url)
    with sessions() as s:
        s.execute(text("UPDATE items SET created_at = :c WHERE id = :i"),
                  {'c': NOW - datetime.timedelta(days=30), 'i': item_id})
        s.commit()
    return item_id


def _tick(sessions, monkeypatch, now=THURSDAY_18_00):
    """Run one tick with the sender stubbed, returning (summary, sends)."""
    from app.services import weekly_note
    sends = []
    monkeypatch.setattr(weekly_note, 'send_weekly_push',
                        lambda user_id, snapshot_id, count: (sends.append(
                            str(snapshot_id)), weekly_note.PushResult(
                                attempted=True, sent=1))[1])
    with sessions() as s:
        return weekly_note.run_weekly_note(s, now=now), sends


def _release(sessions, user_id):
    """Drop this user's weekly claim.

    A successful tick claims the ISO week, so a second tick in the same test
    would report `already_sent` and prove nothing about the schedule. Clearing
    it is what a new week would do, and isolates what each test is actually
    about.
    """
    with sessions() as s:
        s.execute(text('DELETE FROM weekly_snapshots WHERE user_id = :u'),
                  {'u': user_id})
        s.commit()


def _put(client, **value):
    """PUT the whole settings object, as the app does.

    The endpoint takes the complete object (`extra='forbid'`, every field
    required), exactly as `WeeklyNoteSettings.withSchedule` sends it -- so a
    partial body is a 422 rather than a merge. Merging with what is stored here
    keeps each test's intent to a single field.
    """
    user = value.pop('user', None)
    if user is not None:
        client.as_user(user)
    current = client.request('GET', PATH).json()
    return client.request('PUT', PATH, json={**current, **value})


# --- 1. turning the toggle off ---------------------------------------------

def test_turning_the_toggle_off_stops_the_task_on_the_next_tick(
        client, sessions, monkeypatch, two_users):
    _forgotten(sessions, two_users['a'])
    assert _put(client, user=two_users['a'], enabled=True, weekday=3, hour=18,
                minute=0, time_zone='UTC').status_code == 200
    enabled, sends = _tick(sessions, monkeypatch)
    assert enabled['sent'] == 1 and len(sends) == 1

    _put(client, enabled=False)
    off, sends_after = _tick(sessions, monkeypatch)
    assert off['due'] == 0 and off['sent'] == 0
    assert sends_after == [], "a disabled user must not be notified"


def test_the_task_observes_a_disable_with_no_restart(client, sessions, monkeypatch, two_users):
    """Same tick, same process: the row is re-read every time."""
    _forgotten(sessions, two_users['a'])
    _put(client, user=two_users['a'], enabled=True, weekday=3, hour=18,
         time_zone='UTC')
    _put(client, enabled=False)
    summary, _ = _tick(sessions, monkeypatch)
    assert summary['checked'] == 0, "a disabled account is not even examined"


# --- 2. changing the day ----------------------------------------------------

def test_changing_the_day_moves_the_window(client, sessions, monkeypatch, two_users):
    _forgotten(sessions, two_users['a'])
    _put(client, user=two_users['a'], enabled=True, weekday=3, hour=18,
         time_zone='UTC')
    assert _tick(sessions, monkeypatch)[0]['sent'] == 1

    # Thursday -> Monday. The same Thursday tick must now find nothing.
    _put(client, weekday=0)
    _release(sessions, two_users['a'])
    summary, sends = _tick(sessions, monkeypatch)
    assert summary['due'] == 0 and sends == []


def test_the_new_day_is_the_one_that_fires(client, sessions, monkeypatch, two_users):
    _forgotten(sessions, two_users['a'])
    _put(client, user=two_users['a'], enabled=True, weekday=0, hour=18,
         time_zone='UTC')
    assert _tick(sessions, monkeypatch)[0]['sent'] == 0, 'not Monday yet'
    monday = datetime.datetime(2026, 10, 12, 18, 0, tzinfo=datetime.timezone.utc)
    assert _tick(sessions, monkeypatch, now=monday)[0]['sent'] == 1


# --- 3. changing the time ---------------------------------------------------

def test_changing_the_hour_moves_the_window(client, sessions, monkeypatch, two_users):
    _forgotten(sessions, two_users['a'])
    _put(client, user=two_users['a'], enabled=True, weekday=3, hour=18,
         time_zone='UTC')
    assert _tick(sessions, monkeypatch)[0]['sent'] == 1

    _put(client, hour=9)
    _release(sessions, two_users['a'])
    assert _tick(sessions, monkeypatch)[0]['sent'] == 0
    nine = datetime.datetime(2026, 10, 8, 9, 0, tzinfo=datetime.timezone.utc)
    assert _tick(sessions, monkeypatch, now=nine)[0]['sent'] == 1


def test_changing_the_minute_moves_the_window(client, sessions, monkeypatch, two_users):
    _forgotten(sessions, two_users['a'])
    _put(client, user=two_users['a'], enabled=True, weekday=3, hour=18,
         minute=0, time_zone='UTC')
    assert _tick(sessions, monkeypatch)[0]['sent'] == 1

    # 18:07 is served by the 18:15 tick, not the 18:00 one that just fired.
    _put(client, minute=7)
    _release(sessions, two_users['a'])
    assert _tick(sessions, monkeypatch)[0]['sent'] == 0
    quarter = datetime.datetime(2026, 10, 8, 18, 15, tzinfo=datetime.timezone.utc)
    assert _tick(sessions, monkeypatch, now=quarter)[0]['sent'] == 1


# --- 4. changing the time zone ---------------------------------------------

def test_changing_the_zone_moves_the_window(client, sessions, monkeypatch, two_users):
    """Cairo is UTC+3, so 18:00 there is 15:00 UTC, not 18:00."""
    _forgotten(sessions, two_users['a'])
    _put(client, user=two_users['a'], enabled=True, weekday=3, hour=18,
         time_zone='UTC')
    assert _tick(sessions, monkeypatch)[0]['sent'] == 1

    _put(client, time_zone='Africa/Cairo')
    _release(sessions, two_users['a'])
    assert _tick(sessions, monkeypatch)[0]['sent'] == 0, "the UTC tick no longer matches"
    cairo_evening = datetime.datetime(2026, 10, 8, 15, 0, tzinfo=datetime.timezone.utc)
    assert _tick(sessions, monkeypatch, now=cairo_evening)[0]['sent'] == 1


def test_the_zone_is_stored_not_normalised_to_utc(
        client, sessions, monkeypatch, two_users):
    """Storing an offset instead would be wrong twice a year, at DST."""
    _put(client, user=two_users['a'], enabled=True, weekday=3, hour=18,
         time_zone='America/New_York')
    stored = client.request('GET', PATH).json()
    assert stored['time_zone'] == 'America/New_York'
    # 18:00 in New York is 22:00 UTC in October (EDT). A UTC schedule would
    # have fired at 18:00 UTC instead, an hour early.
    _forgotten(sessions, two_users['a'])
    assert _tick(sessions, monkeypatch)[0]['sent'] == 0
    ny_evening = datetime.datetime(2026, 10, 8, 22, 0, tzinfo=datetime.timezone.utc)
    assert _tick(sessions, monkeypatch, now=ny_evening)[0]['sent'] == 1


# --- 5. one user's change never touches another's --------------------------

def test_a_change_is_scoped_to_the_caller(client, sessions, monkeypatch, two_users):
    """Turning A's switch off must not silence B."""
    _forgotten(sessions, two_users['a'], 'https://old.test/a')
    _forgotten(sessions, two_users['b'], 'https://old.test/b')
    # Both accounts want a Thursday 18:00 note.
    _put(client, user=two_users['a'], enabled=True, weekday=3, hour=18,
         time_zone='UTC')
    _put(client, user=two_users['b'], enabled=True, weekday=3, hour=18,
         time_zone='UTC')
    _put(client, user=two_users['a'], enabled=False)

    assert client.request('GET', PATH).json()['enabled'] is False
    _put(client, user=two_users['b'], enabled=True, weekday=3, hour=18,
         time_zone='UTC')
    assert client.request('GET', PATH).json()['enabled'] is True, \
        "A's change did not touch B"

    summary, sends = _tick(sessions, monkeypatch)
    assert summary['sent'] == 1, "only B should still be notified"
    assert summary['checked'] == 1, "A is disabled and must not be examined"