"""The weekly-note tick: whose moment is now, in their own zone, exactly once.

Four things decide whether a user is notified, and each is a separate kind of
wrong answer if it breaks:

  * the window -- the task runs every fifteen minutes and the user's choice is
    an exact hour and minute, so "is this tick the right one" is the rule;
  * the zone -- a stored UTC offset would put the note an hour off for half the
    year, so the tests pin real DST transition instants;
  * the guard -- a beat restart or a second beat instance must not send twice;
  * the zero count -- nothing forgotten means nothing sent.
"""
import datetime

from sqlalchemy import text

from app.models import SnapshotItem, WeeklyNotePreference, WeeklySnapshot
from app.services.weekly_note import due_now, iso_week, run_weekly_note
from test_phase16_multitenant import admin_engine, db, sessions, two_users, _ready_item

NOW = datetime.datetime(2026, 10, 8, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _prefer(sessions, user_id, **overrides):
    """A weekly-note preference row, defaulting to disabled like the API's."""
    values = {'user_id': user_id, 'enabled': False, 'weekday': 6,
              'hour': 18, 'minute': 0, 'time_zone': 'UTC'} | overrides
    with sessions() as s:
        s.execute(text("""
            INSERT INTO weekly_note_preferences
                (user_id, enabled, weekday, hour, minute, time_zone)
            VALUES (:user_id, :enabled, :weekday, :hour, :minute, :time_zone)
            ON CONFLICT (user_id) DO UPDATE SET
                enabled = :enabled, weekday = :weekday, hour = :hour,
                minute = :minute, time_zone = :time_zone
        """), values)
        s.commit()


def _forgotten(sessions, user_id, url, *, created):
    item_id, _ = _ready_item(sessions, user_id, url)
    with sessions() as s:
        s.execute(text("UPDATE items SET created_at = :c WHERE id = :i"),
                  {"c": created, "i": item_id})
        s.commit()
    return item_id


def _sent(monkeypatch, result=None):
    """Record every send_weekly_push call instead of performing one.

    Defaults to a successful delivery, which is the case the claim-keeping
    tests care about. Pass a `PushResult` to drive the release path.
    """
    calls = []
    from app.services import weekly_note
    outcome = result if result is not None else weekly_note.PushResult(
        attempted=True, sent=1, reason='delivered')
    monkeypatch.setattr(weekly_note, 'send_weekly_push',
                        lambda user_id, snapshot_id, count: (calls.append(
                            (str(user_id), str(snapshot_id), count)), outcome)[1])
    return calls


def _tick(sessions, monkeypatch, now=NOW):
    with sessions() as s:
        return run_weekly_note(s, now=now)


# --- 1. the window ----------------------------------------------------------

def test_a_user_is_notified_in_their_chosen_window(sessions, monkeypatch, two_users):
    _forgotten(sessions, two_users["a"], "https://old.test/a", created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users["a"], enabled=True, weekday=3, hour=18, minute=30,
            time_zone='UTC')
    calls = _sent(monkeypatch)

    # 2026-10-08 is a Thursday (weekday 3); 18:30 sits inside the 18:30 tick.
    assert _tick(sessions, monkeypatch,
                 now=datetime.datetime(2026, 10, 8, 18, 30, tzinfo=datetime.timezone.utc))['sent'] == 1
    assert len(calls) == 1
    assert calls[0][2] == 1, "the note carries the number of saves it counted"


def test_a_tick_outside_the_window_sends_nothing(sessions, monkeypatch, two_users):
    _forgotten(sessions, two_users["a"], "https://old.test/a", created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users["a"], enabled=True, weekday=3, hour=18, minute=30,
            time_zone='UTC')
    calls = _sent(monkeypatch)

    # Same Thursday, an hour early and an hour late.
    for hour in (17, 19):
        summary = _tick(sessions, monkeypatch, now=datetime.datetime(
            2026, 10, 8, hour, 30, tzinfo=datetime.timezone.utc))
        assert summary['due'] == 0 and summary['sent'] == 0
    # And on the wrong weekday entirely.
    assert _tick(sessions, monkeypatch, now=datetime.datetime(
        2026, 10, 9, 18, 30, tzinfo=datetime.timezone.utc))['due'] == 0
    assert calls == []


def test_a_minute_off_the_tick_is_still_inside_the_window(sessions, monkeypatch, two_users):
    """The user chose 09:07; the tick that serves it is 09:15, not 09:00."""
    _forgotten(sessions, two_users["a"], "https://old.test/a", created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users["a"], enabled=True, weekday=3, hour=9, minute=7,
            time_zone='UTC')
    calls = _sent(monkeypatch)
    assert _tick(sessions, monkeypatch, now=datetime.datetime(
        2026, 10, 8, 9, 15, tzinfo=datetime.timezone.utc))['sent'] == 1
    assert len(calls) == 1


def test_sunday_note_after_midnight_claims_sundays_iso_week(sessions, monkeypatch, two_users):
    _forgotten(sessions, two_users['a'], 'https://old.test/sunday',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=6, hour=23, minute=50)
    calls = _sent(monkeypatch)
    now = datetime.datetime(2026, 10, 5, 0, 0, tzinfo=datetime.timezone.utc)
    assert _tick(sessions, monkeypatch, now=now)['sent'] == 1
    with sessions() as s:
        assert s.query(WeeklySnapshot).one().iso_week == '2026-W40'
    assert _tick(sessions, monkeypatch, now=now)['already_sent'] == 1
    assert len(calls) == 1


# --- 2. the disabled toggle -------------------------------------------------

def test_a_disabled_user_is_never_notified(sessions, monkeypatch, two_users):
    """enabled=false is the default, and it must mean silence."""
    _forgotten(sessions, two_users["a"], "https://old.test/a", created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users["a"], enabled=False, weekday=3, hour=18, minute=0,
            time_zone='UTC')
    calls = _sent(monkeypatch)
    summary = _tick(sessions, monkeypatch, now=datetime.datetime(
        2026, 10, 8, 18, 0, tzinfo=datetime.timezone.utc))
    assert summary['checked'] == 0, "a disabled user is not even checked"
    assert calls == []


def test_toggling_off_then_back_on_respects_the_latest_choice(sessions, monkeypatch, two_users):
    _forgotten(sessions, two_users["a"], "https://old.test/a", created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users["a"], enabled=True, weekday=3, hour=18, time_zone='UTC')
    calls = _sent(monkeypatch)
    tick = datetime.datetime(2026, 10, 8, 18, 0, tzinfo=datetime.timezone.utc)
    assert _tick(sessions, monkeypatch, now=tick)['sent'] == 1

    _prefer(sessions, two_users["a"], enabled=False)
    assert _tick(sessions, monkeypatch, now=tick)['sent'] == 0
    assert len(calls) == 1


# --- 3. zero count ----------------------------------------------------------

def test_nothing_forgotten_sends_nothing_and_claims_nothing(
        sessions, monkeypatch, two_users):
    """A zero count sends no note, and leaves the week unclaimed."""
    _prefer(sessions, two_users["a"], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    calls = _sent(monkeypatch)
    now = datetime.datetime(2026, 10, 8, 18, 0, tzinfo=datetime.timezone.utc)

    summary = _tick(sessions, monkeypatch, now=now)
    assert summary['nothing_to_show'] == 1
    assert summary['sent'] == 0
    assert calls == []
    with sessions() as s:
        assert s.query(WeeklySnapshot).count() == 0, "an empty note must not be stored"

    # A save becomes forgotten later in the same window, and the next tick sends.
    _forgotten(sessions, two_users["a"], "https://old.test/late",
               created=NOW - datetime.timedelta(days=30))
    assert _tick(sessions, monkeypatch, now=now)['sent'] == 1
    assert len(calls) == 1


def test_a_zero_count_does_not_stop_a_later_run_in_the_same_week(
        sessions, monkeypatch, two_users):
    """The first tick found nothing; a later tick in the same window must retry.

    The chosen minute is 07, so the window spans two ticks (09:00 and 09:15)
    and the retry can happen inside it.
    """
    _prefer(sessions, two_users["a"], enabled=True, weekday=3, hour=9, minute=7,
            time_zone='UTC')
    calls = _sent(monkeypatch)
    _tick(sessions, monkeypatch, now=datetime.datetime(
        2026, 10, 8, 9, 0, tzinfo=datetime.timezone.utc))
    assert calls == []

    _forgotten(sessions, two_users["a"], "https://old.test/later",
               created=NOW - datetime.timedelta(days=30))
    _tick(sessions, monkeypatch, now=datetime.datetime(
        2026, 10, 8, 9, 15, tzinfo=datetime.timezone.utc))
    assert len(calls) == 1


# --- 4. duplicate runs ------------------------------------------------------

def test_running_twice_in_one_window_sends_once(sessions, monkeypatch, two_users):
    """The guard the user feels: one note, not one per tick."""
    _forgotten(sessions, two_users["a"], "https://old.test/a", created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users["a"], enabled=True, weekday=3, hour=18, time_zone='UTC')
    calls = _sent(monkeypatch)
    now = datetime.datetime(2026, 10, 8, 18, 0, tzinfo=datetime.timezone.utc)

    assert _tick(sessions, monkeypatch, now=now)['sent'] == 1
    second = _tick(sessions, monkeypatch, now=now)
    assert second['already_sent'] == 1
    assert second['sent'] == 0
    assert len(calls) == 1, "a repeated run must not notify twice"


def test_a_new_week_sends_again(sessions, monkeypatch, two_users):
    """The guard is per ISO week, not a one-shot switch."""
    _forgotten(sessions, two_users["a"], "https://old.test/a", created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users["a"], enabled=True, weekday=3, hour=18, time_zone='UTC')
    calls = _sent(monkeypatch)
    _tick(sessions, monkeypatch, now=datetime.datetime(
        2026, 10, 8, 18, 0, tzinfo=datetime.timezone.utc))
    _tick(sessions, monkeypatch, now=datetime.datetime(
        2026, 10, 15, 18, 0, tzinfo=datetime.timezone.utc))
    assert len(calls) == 2
    assert calls[0][1] != calls[1][1], "each note points at its own snapshot"


def test_the_week_key_is_unique_per_user_not_globally(sessions, monkeypatch, two_users):
    """Two users in the same week each get their note."""
    for key, url in (('a', 'https://old.test/a'), ('b', 'https://old.test/b')):
        _forgotten(sessions, two_users[key], url, created=NOW - datetime.timedelta(days=30))
        _prefer(sessions, two_users[key], enabled=True, weekday=3, hour=18,
                time_zone='UTC')
    calls = _sent(monkeypatch)
    assert _tick(sessions, monkeypatch, now=datetime.datetime(
        2026, 10, 8, 18, 0, tzinfo=datetime.timezone.utc))['sent'] == 2
    assert len({call[0] for call in calls}) == 2


def test_a_duplicate_run_leaves_no_extra_snapshot(sessions, monkeypatch, two_users):
    _forgotten(sessions, two_users["a"], "https://old.test/a", created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users["a"], enabled=True, weekday=3, hour=18, time_zone='UTC')
    _sent(monkeypatch)
    now = datetime.datetime(2026, 10, 8, 18, 0, tzinfo=datetime.timezone.utc)
    _tick(sessions, monkeypatch, now=now)
    _tick(sessions, monkeypatch, now=now)
    with sessions() as s:
        assert s.query(WeeklySnapshot).filter(
            WeeklySnapshot.user_id == two_users["a"]).count() == 1
        assert s.query(SnapshotItem).count() == 1


# --- 5. the zone, including DST -------------------------------------------

def _preference(user_id, **overrides):
    """The matching rule on its own, with no database behind it."""
    return WeeklyNotePreference(user_id=user_id, **{
        'enabled': True, 'weekday': 6, 'hour': 9, 'minute': 30,
        'time_zone': 'UTC'} | overrides)


def test_the_window_is_evaluated_in_the_users_own_zone():
    """09:00 in Cairo is 06:00 UTC that day; a UTC-only schedule would wait."""
    preference = _preference('u', time_zone='Africa/Cairo', hour=9, minute=0)
    assert due_now(preference, datetime.datetime(
        2026, 10, 4, 6, 0, tzinfo=datetime.timezone.utc))
    assert not due_now(preference, datetime.datetime(
        2026, 10, 4, 9, 0, tzinfo=datetime.timezone.utc))


def test_the_window_survives_the_spring_dst_change():
    """The user chose 09:30 New York, on a Sunday either side of the change.

    A stored UTC offset would send at 14:30 UTC all year and be an hour wrong
    for half of it. 2026-03-08 is the change: 09:30 EST is 14:30 UTC the week
    before, and 09:30 EDT is 13:30 UTC on the day.
    """
    preference = _preference('u', time_zone='America/New_York', hour=9, minute=30)
    assert due_now(preference, datetime.datetime(
        2026, 3, 1, 14, 30, tzinfo=datetime.timezone.utc))
    assert due_now(preference, datetime.datetime(
        2026, 3, 8, 13, 30, tzinfo=datetime.timezone.utc))
    assert not due_now(preference, datetime.datetime(
        2026, 3, 8, 14, 30, tzinfo=datetime.timezone.utc)), (
        "the old offset must not keep firing an hour off")


def test_the_window_survives_the_autumn_dst_change():
    """Same again in the other direction: 09:30 is 13:30 UTC before, 14:30 after."""
    preference = _preference('u', time_zone='America/New_York', hour=9, minute=30)
    assert due_now(preference, datetime.datetime(
        2026, 10, 25, 13, 30, tzinfo=datetime.timezone.utc))
    assert due_now(preference, datetime.datetime(
        2026, 11, 1, 14, 30, tzinfo=datetime.timezone.utc))
    assert not due_now(preference, datetime.datetime(
        2026, 11, 1, 13, 30, tzinfo=datetime.timezone.utc))


def test_an_unusable_zone_is_skipped_rather_than_guessed():
    """A zone this host does not know must not send at some other hour."""
    preference = _preference('u', time_zone='Invented/Zone', hour=9)
    assert not due_now(preference, datetime.datetime(
        2026, 10, 4, 9, 30, tzinfo=datetime.timezone.utc))


def test_the_week_key_follows_the_local_calendar_not_utc():
    """A Monday morning in Auckland is already the new week in local terms.

    2026-10-04 20:00 UTC is Monday 09:00 in Auckland, and UTC is still on
    Sunday -- the last day of the previous ISO week.
    """
    from zoneinfo import ZoneInfo
    utc_instant = datetime.datetime(2026, 10, 4, 20, 0, tzinfo=datetime.timezone.utc)
    assert iso_week(utc_instant) == '2026-W40'
    assert iso_week(utc_instant.astimezone(ZoneInfo('Pacific/Auckland'))) == '2026-W41'


# --- 6. the schedule itself -------------------------------------------------

def test_beat_runs_the_weekly_note_every_fifteen_minutes():
    """The tick period is part of the contract: `due_now` assumes it."""
    from app.celery_app import celery

    entry = celery.conf.beat_schedule['weekly-note']
    assert entry['task'] == 'run_weekly_note'
    assert entry['schedule'].minute == {0, 15, 30, 45}


def test_the_scheduled_task_is_registered_on_the_worker():
    """A beat entry naming an unregistered task fires and fails every 15 min."""
    from app import tasks
    from app.celery_app import celery

    assert 'run_weekly_note' in celery.tasks
    assert callable(tasks.run_weekly_note_task)


def test_a_dst_fall_back_hour_sends_once_not_twice(sessions, monkeypatch, two_users):
    """01:30 happens twice on the US fall-back day, in the same ISO week.

    Both instants are inside the user's window, and both are the same week, so
    the unique key is what stops the user getting the note twice.
    """
    _forgotten(sessions, two_users["a"], "https://old.test/a", created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users["a"], enabled=True, weekday=6, hour=1, minute=30,
            time_zone='America/New_York')
    calls = _sent(monkeypatch)

    first = datetime.datetime(2026, 11, 1, 5, 30, tzinfo=datetime.timezone.utc)   # 01:30 EDT
    second = datetime.datetime(2026, 11, 1, 6, 30, tzinfo=datetime.timezone.utc)  # 01:30 EST
    assert _tick(sessions, monkeypatch, now=first)['sent'] == 1
    assert _tick(sessions, monkeypatch, now=second)['already_sent'] == 1
    assert len(calls) == 1, "the repeated local hour must not notify twice"
