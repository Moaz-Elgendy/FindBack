"""When the week's claim is kept, and when it is given back.

The unique key on (user_id, iso_week) is what stops a double send, but a claim
only belongs to a note that was actually delivered. Holding one for a note that
never went out loses that user's note for the entire week because of a missing
config value, a device that has not registered yet, or one bad minute at FCM.

So the rule is:

    delivered to at least one device  -> keep the claim (no second notification)
    anything else                      -> release it, and a later tick retries

"Anything else" covers not attempting delivery at all (FCM unconfigured, key
unusable, no devices) and attempting it but failing transiently (5xx, quota).
Both release, because both are conditions that can be fixed inside the same
week.

Retries are bounded by `due_now`, not by a counter: once the user's chosen
window closes, no further tick matches for that week, so a permanently broken
deployment cannot turn into an endless loop.
"""
import datetime
import threading

from sqlalchemy import text

from app.models import SnapshotItem, WeeklySnapshot
from app.services import weekly_note
from app.services.weekly_note import PushResult, run_weekly_note
from test_phase16_multitenant import admin_engine, db, sessions, two_users, _ready_item
from test_weekly_note import NOW, _forgotten, _prefer

TICK = datetime.datetime(2026, 10, 8, 18, 0, tzinfo=datetime.timezone.utc)


def _snapshots(sessions, user_id):
    """Every snapshot row for a user, as (id, iso_week)."""
    with sessions() as s:
        return [(str(row[0]), row[1]) for row in s.query(
            WeeklySnapshot.id, WeeklySnapshot.iso_week)
            .filter(WeeklySnapshot.user_id == user_id).all()]


def _snapshot_items(sessions):
    with sessions() as s:
        return s.query(SnapshotItem).count()


def _delivering(monkeypatch, result):
    """Replace the sender, returning the recorded calls."""
    calls = []
    monkeypatch.setattr(weekly_note, 'send_weekly_push',
                        lambda user_id, snapshot_id, count: (calls.append(
                            (str(user_id), str(snapshot_id), count)), result)[1])
    return calls


def _run(sessions, monkeypatch, result, now=TICK):
    """Run one tick with a stubbed sender. Returns (summary, send_calls)."""
    calls = _delivering(monkeypatch, result)
    with sessions() as s:
        return run_weekly_note(s, now=now), calls


# --- 1. success keeps the claim ---------------------------------------------

def test_a_delivered_note_keeps_the_claim(sessions, monkeypatch, two_users):
    """The whole point of the unique key: a second run must not notify again."""
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    summary, _ = _run(sessions, monkeypatch, PushResult(attempted=True, sent=1,
                                                        reason='delivered'))

    assert summary['sent'] == 1
    assert len(_snapshots(sessions, two_users['a'])) == 1
    assert _snapshot_items(sessions) == 1, "the frozen list must survive"


def test_a_second_run_after_success_is_already_sent(sessions, monkeypatch, two_users):
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    sent, _ = _run(sessions, monkeypatch, PushResult(attempted=True, sent=1))
    second, _ = _run(sessions, monkeypatch, PushResult(attempted=True, sent=1))

    assert sent['sent'] == 1
    assert second['already_sent'] == 1 and second['sent'] == 0
    assert len(_snapshots(sessions, two_users['a'])) == 1


def test_one_device_succeeding_keeps_the_claim_even_if_others_fail(
        sessions, monkeypatch, two_users):
    """Partial delivery is delivery: the user was notified."""
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    result = PushResult(attempted=True, sent=1, failed=2, removed=1,
                        reason='delivered')
    summary, _ = _run(sessions, monkeypatch, result)

    assert summary['sent'] == 1
    assert len(_snapshots(sessions, two_users['a'])) == 1


# --- 2. delivery never attempted releases the claim ------------------------

def test_unconfigured_push_releases_the_claim(sessions, monkeypatch, two_users):
    """The reported bug: a missing env var must not cost the user their week."""
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    summary, _ = _run(sessions, monkeypatch,
                      PushResult(attempted=False, reason='unconfigured'))

    assert summary['retry'] == 1 and summary['sent'] == 0
    assert _snapshots(sessions, two_users['a']) == [], "the week is still claimed"
    assert _snapshot_items(sessions) == 0, "a released claim leaves no items"


def test_a_user_with_no_devices_releases_the_claim(sessions, monkeypatch, two_users):
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    summary, _ = _run(sessions, monkeypatch,
                      PushResult(attempted=False, reason='no_devices'))

    assert summary['retry'] == 1
    assert _snapshots(sessions, two_users['a']) == []


def test_unusable_credentials_release_the_claim(sessions, monkeypatch, two_users):
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    summary, _ = _run(sessions, monkeypatch,
                      PushResult(attempted=False, reason='credentials_unusable'))
    assert summary['retry'] == 1
    assert _snapshots(sessions, two_users['a']) == []


# --- 3. transient failure releases the claim, so a retry can win ------------

def test_a_transient_failure_releases_the_claim(sessions, monkeypatch, two_users):
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    summary, _ = _run(sessions, monkeypatch,
                      PushResult(attempted=True, sent=0, failed=1,
                                 reason='not_delivered'))
    assert summary['retry'] == 1
    assert _snapshots(sessions, two_users['a']) == []


def test_a_released_claim_can_be_won_by_a_later_run_in_the_same_window(
        sessions, monkeypatch, two_users):
    """The point of releasing: once FCM recovers, the note still goes out."""
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')

    first, _ = _run(sessions, monkeypatch,
                    PushResult(attempted=True, sent=0, failed=1,
                               reason='not_delivered'))
    assert first['retry'] == 1

    second, _ = _run(sessions, monkeypatch,
                     PushResult(attempted=True, sent=1, reason='delivered'))
    assert second['sent'] == 1
    assert second['retry'] == 0
    assert len(_snapshots(sessions, two_users['a'])) == 1


def test_retrying_creates_a_fresh_snapshot_not_the_released_one(
        sessions, monkeypatch, two_users):
    """A released id must never be reused: nothing was told to fetch it."""
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')

    failed_summary, failed_calls = _run(
        sessions, monkeypatch, PushResult(attempted=True, sent=0, failed=1))
    assert failed_summary['retry'] == 1
    assert len(failed_calls) == 1

    sent_summary, sent_calls = _run(sessions, monkeypatch,
                                    PushResult(attempted=True, sent=1))
    assert sent_summary['sent'] == 1
    delivered_id = sent_calls[0][1]
    assert delivered_id != failed_calls[0][1], \
        'a released snapshot id must never be handed out twice'
    # Only one row exists, and it is the one that was actually delivered.
    assert [row[0] for row in _snapshots(sessions, two_users['a'])] == [delivered_id]


# --- 4. retries are bounded by the due window ------------------------------

def test_a_released_claim_is_not_retried_after_the_window_closes(
        sessions, monkeypatch, two_users):
    """No counter, no loop: outside the window the user is simply not due.

    This is what stops a permanently broken deployment from retrying forever.
    The claim stays released, so the note is lost for this week rather than
    being retried on every tick until something changes -- and the next week's
    window picks it up normally.
    """
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    _run(sessions, monkeypatch, PushResult(attempted=True, sent=0, failed=1))
    assert _snapshots(sessions, two_users['a']) == []

    # An hour later the user's moment has passed.
    later = TICK + datetime.timedelta(hours=1)
    summary, _ = _run(sessions, monkeypatch, PushResult(attempted=True, sent=1),
                      now=later)
    assert summary['due'] == 0 and summary['sent'] == 0
    assert _snapshots(sessions, two_users['a']) == []


def test_the_next_week_still_delivers_after_a_failure(sessions, monkeypatch, two_users):
    """Bounded does not mean dead: a week later the user is notified again."""
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    _run(sessions, monkeypatch, PushResult(attempted=True, sent=0, failed=1))
    next_week, _ = _run(sessions, monkeypatch, PushResult(attempted=True, sent=1),
                         now=TICK + datetime.timedelta(days=7))
    assert next_week['sent'] == 1


def test_zero_count_still_does_not_claim_the_week(sessions, monkeypatch, two_users):
    """Unchanged by all of this: nothing forgotten must write nothing at all."""
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    summary, calls = _run(sessions, monkeypatch, PushResult(attempted=True, sent=1))
    assert summary['nothing_to_show'] == 1
    assert calls == [], "no send was attempted"
    assert _snapshots(sessions, two_users['a']) == []


# --- 5. concurrent runs still send once ------------------------------------

def test_two_concurrent_runs_produce_exactly_one_send(sessions, monkeypatch, two_users):
    """Releasing a claim must not open the door to a double notification.

    Two ticks overlap: the first claims the week and is still inside the
    delivery call when the second starts. The second loses the INSERT race on
    the unique key, so it never reaches the sender at all -- whatever the first
    one then decides about keeping or releasing.
    """
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')

    first_sending = threading.Event()
    second_done = threading.Event()
    sent = []

    def blocking_send(user_id, snapshot_id, count):
        sent.append(str(snapshot_id))
        if len(sent) == 1:
            # Hold the first run inside delivery while the second one runs.
            first_sending.set()
            second_done.wait(timeout=10)
        return PushResult(attempted=True, sent=1, reason='delivered')

    monkeypatch.setattr(weekly_note, 'send_weekly_push', blocking_send)
    results = {}

    def first():
        with sessions() as s:
            results['first'] = run_weekly_note(s, now=TICK)

    def second():
        first_sending.wait(timeout=10)
        with sessions() as s:
            results['second'] = run_weekly_note(s, now=TICK)
        second_done.set()

    one = threading.Thread(target=first)
    two = threading.Thread(target=second)
    one.start()
    two.start()
    one.join(timeout=30)
    two.join(timeout=30)

    assert len(sent) == 1, f"the user would be notified {len(sent)} times"
    assert results['first']['sent'] == 1
    assert results['second']['already_sent'] == 1
    assert results['second']['sent'] == 0
    assert len(_snapshots(sessions, two_users['a'])) == 1


def test_a_concurrent_run_cannot_send_twice_even_when_the_first_releases(
        sessions, monkeypatch, two_users):
    """The release path must not become a double-send path."""
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')

    first_sending = threading.Event()
    second_done = threading.Event()
    sent = []

    def blocking_send(user_id, snapshot_id, count):
        sent.append(str(snapshot_id))
        if len(sent) == 1:
            first_sending.set()
            second_done.wait(timeout=10)
        # Fails, so the first run releases its claim afterwards.
        return PushResult(attempted=True, sent=0, failed=1, reason='not_delivered')

    monkeypatch.setattr(weekly_note, 'send_weekly_push', blocking_send)
    results = {}

    def first():
        with sessions() as s:
            results['first'] = run_weekly_note(s, now=TICK)

    def second():
        first_sending.wait(timeout=10)
        with sessions() as s:
            results['second'] = run_weekly_note(s, now=TICK)
        second_done.set()

    one = threading.Thread(target=first)
    two = threading.Thread(target=second)
    one.start()
    two.start()
    one.join(timeout=30)
    two.join(timeout=30)

    assert len(sent) == 1, "the second run reached the sender despite the claim"
    assert results['second']['already_sent'] == 1
    # The first run failed, so it gave the week back for a later retry.
    assert results['first']['retry'] == 1
    assert _snapshots(sessions, two_users['a']) == []


# --- 6. the released week is genuinely reusable -----------------------------

def test_the_week_key_is_free_after_a_release(sessions, monkeypatch, two_users):
    """Not just "no rows": the same (user, ISO week) can be claimed again."""
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    _run(sessions, monkeypatch, PushResult(attempted=False, reason='no_devices'))
    assert _snapshots(sessions, two_users['a']) == []

    # Claiming the identical key again must succeed, not raise IntegrityError.
    with sessions() as s:
        snapshot_id = weekly_note.create_snapshot(s, two_users['a'], now=TICK,
                                                  iso_week=weekly_note.iso_week(
                                                      weekly_note.local_time(
                                                          _preference(s, two_users['a']),
                                                          TICK)))
        assert snapshot_id is not None
    assert len(_snapshots(sessions, two_users['a'])) == 1


def _preference(session, user_id):
    from app.models import WeeklyNotePreference
    return session.query(WeeklyNotePreference).filter(
        WeeklyNotePreference.user_id == user_id).one()