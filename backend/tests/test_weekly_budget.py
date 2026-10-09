"""Two ways a tick runs out of time, and what it leaves behind.

Sequential sends with a per-request timeout mean one account with many devices
can spend a large slice of the task's soft time limit. Reaching that limit is
worse than it looks: Celery raises `SoftTimeLimitExceeded` inside whatever
happens to be running, killing the worker while it may hold an open claim --
and a claim held by a process that never returns silences that account for the
whole week.

So there are three defences, and each is tested here:

  * a smaller per-request budget and a cap on devices per user;
  * a wall-clock budget that stops starting NEW users before the limit, leaving
    the remainder to the next tick while their window is still open;
  * a sweep for claims that a hard crash left behind, which no exception
    handler could catch.
"""
import datetime
import time

import httpx
import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import text

from app.models import DeviceToken
from app.services import weekly_note
from app.services.weekly_note import PushResult, run_weekly_note
from test_phase16_multitenant import admin_engine, db, sessions, two_users
from test_weekly_note import NOW, _forgotten, _prefer

TICK = datetime.datetime(2026, 10, 8, 18, 0, tzinfo=datetime.timezone.utc)


def _devices(sessions, user_id, count, prefix='device'):
    for i in range(count):
        with sessions() as s:
            s.add(DeviceToken(user_id=user_id, token=f'{prefix}-{i}',
                              platform='android'))
            s.commit()


def _snapshots(sessions, user_id):
    with sessions() as s:
        return s.query(weekly_note.WeeklySnapshot).filter(
            weekly_note.WeeklySnapshot.user_id == user_id).all()


def _claim(sessions, user_id, created, delivered_at=None):
    """A claim as a stranded worker would have left it."""
    with sessions() as s:
        s.add(weekly_note.WeeklySnapshot(
            user_id=user_id, iso_week='2026-W41', created_at=created,
            delivered_at=delivered_at))
        s.commit()


def _run(sessions, monkeypatch, now=TICK, budget_seconds=None, result=None):
    monkeypatch.setattr(weekly_note, 'send_weekly_push',
                        lambda u, s, c: (result or PushResult(
                            attempted=True, sent=1)))
    with sessions() as s:
        return run_weekly_note(s, now=now, budget_seconds=budget_seconds)


# --- 1. the per-request budget and the device cap ---------------------------

def test_the_per_request_timeout_is_five_seconds():
    """Ten seconds each, times ten devices, is a hundred seconds per account."""
    assert weekly_note.FCM_TIMEOUT == httpx.Timeout(5.0)
    assert weekly_note.FCM_TIMEOUT.read == 5.0


def test_at_most_ten_devices_are_sent_to(monkeypatch, sessions, two_users):
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    _devices(sessions, two_users['a'], 15)

    sent = []
    monkeypatch.setenv('FCM_SERVICE_ACCOUNT_FILE', '/tmp/sa.json')
    monkeypatch.setattr(weekly_note, '_CREDENTIALS', type(
        'C', (), {'service_account_email': 'sa@p.iam.gserviceaccount.com',
                  'token': 't', 'valid': True})())
    monkeypatch.setattr(weekly_note, 'SessionLocal', sessions)

    class _T:
        def __init__(self):
            self.tokens = []

        def __call__(self, request):
            import json
            token = json.loads(request.content)['message']['token']
            self.tokens.append(token)
            raise weekly_note.httpx.ConnectError('offline')

    transport = _T()
    monkeypatch.setattr(weekly_note, '_TRANSPORT',
                        weekly_note.httpx.MockTransport(transport))

    with sessions() as s:
        run_weekly_note(s, now=TICK)

    assert len(transport.tokens) == weekly_note.MAX_DEVICES_PER_USER
    assert sent == []


def test_the_cap_keeps_the_most_recently_registered_devices(
        monkeypatch, sessions, two_users):
    monkeypatch.setattr(weekly_note, 'SessionLocal', sessions)
    """The oldest registrations are the likeliest to have been uninstalled."""
    _devices(sessions, two_users['a'], 12, prefix='old')
    with sessions() as s:
        s.execute(text(
            "UPDATE device_tokens SET updated_at = created_at - interval '1 day'"))
        s.add(DeviceToken(user_id=two_users['a'], token='newest',
                          platform='android'))
        s.commit()

    tokens = [row[0] for row in weekly_note.db_session_device_tokens(
        two_users['a'])]
    assert len(tokens) == weekly_note.MAX_DEVICES_PER_USER
    assert tokens[0] == 'newest', "newest first"
    assert 'old-0' not in tokens, "the stalest registration was cut"


# --- 2. the time budget stops starting new users ---------------------------

def test_the_budget_is_eighty_percent_of_the_soft_limit(monkeypatch):
    monkeypatch.setenv('TASK_SOFT_TIME_LIMIT', '900')
    assert weekly_note.TASK_BUDGET_FRACTION == 0.8
    assert weekly_note._soft_time_limit() == 900


def test_users_left_over_by_the_budget_are_not_started(
        sessions, monkeypatch, two_users):
    """A budget already spent: nobody is started, so no claim is opened."""
    for key in ('a', 'b'):
        _forgotten(sessions, two_users[key], f'https://old.test/{key}',
                   created=NOW - datetime.timedelta(days=30))
        _prefer(sessions, two_users[key], enabled=True, weekday=3, hour=18,
                time_zone='UTC')

    # budget_seconds = -1 means the budget is already spent on arrival.
    summary = _run(sessions, monkeypatch, budget_seconds=-1)

    assert summary['skipped_for_budget'] == 2
    assert summary['sent'] == 0
    for key in ('a', 'b'):
        assert _snapshots(sessions, two_users[key]) == [], \
            'no claim may be opened by a user that is never processed'


def test_the_budget_leaves_earlier_users_alone(
        sessions, monkeypatch, two_users):
    """The budget cuts off the tail; it must not skip anyone at the front."""
    for key in ('a', 'b'):
        _forgotten(sessions, two_users[key], f'https://old.test/{key}',
                   created=NOW - datetime.timedelta(days=30))
        _prefer(sessions, two_users[key], enabled=True, weekday=3, hour=18,
                time_zone='UTC')

    clock = {'t': 0.0}

    def fake_monotonic():
        # Each user costs one unit; a budget of 0.5 admits only the first.
        return clock['t']

    def counting_send(user_id, snapshot_id, count):
        clock['t'] += 1.0
        return PushResult(attempted=True, sent=1)

    monkeypatch.setattr(time, 'monotonic', fake_monotonic)
    monkeypatch.setattr(weekly_note, 'send_weekly_push', counting_send)

    with sessions() as s:
        summary = run_weekly_note(s, now=TICK, budget_seconds=0.5)

    assert summary['sent'] == 1, "the first user was served"
    assert summary['skipped_for_budget'] == 1
    assert len(_snapshots(sessions, two_users['a'])) == 1
    assert _snapshots(sessions, two_users['b']) == [], \
        'a skipped user must not have a claim opened for it'


def test_a_user_left_over_is_still_reachable_on_the_next_tick(
        sessions, monkeypatch, two_users):
    """Skipping is only safe because the next tick retries them."""
    for key in ('a', 'b'):
        _forgotten(sessions, two_users[key], f'https://old.test/{key}',
                   created=NOW - datetime.timedelta(days=30))
        _prefer(sessions, two_users[key], enabled=True, weekday=3, hour=18,
                time_zone='UTC')

    assert _run(sessions, monkeypatch, budget_seconds=-1)['sent'] == 0
    # Next tick, budget healthy: nobody was claimed, so everybody is served.
    assert _run(sessions, monkeypatch)['sent'] == 2


# --- 3. SoftTimeLimitExceeded releases the claim, then re-raises ------------

def test_a_soft_time_limit_releases_the_claim_and_reraises(
        sessions, monkeypatch, two_users):
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')

    def hit_the_limit(user_id, snapshot_id, count):
        # What Celery does when the soft limit passes mid-send.
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(weekly_note, 'send_weekly_push', hit_the_limit)

    with pytest.raises(SoftTimeLimitExceeded):
        with sessions() as s:
            run_weekly_note(s, now=TICK)

    assert _snapshots(sessions, two_users['a']) == [], \
        'the worker is about to be killed holding this claim'


def test_the_soft_limit_is_not_swallowed_as_an_ordinary_failure(
        sessions, monkeypatch, two_users):
    """`SoftTimeLimitExceeded` subclasses Exception, so ordering matters.

    Without a dedicated handler it lands in the generic `except Exception`,
    the tick reports `failed` and returns normally -- and Celery's own
    teardown then kills the worker mid-tick anyway.
    """
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    monkeypatch.setattr(weekly_note, 'send_weekly_push',
                        lambda u, s, c: (_ for _ in ()).throw(
                            SoftTimeLimitExceeded()))

    with pytest.raises(SoftTimeLimitExceeded):
        with sessions() as s:
            summary = run_weekly_note(s, now=TICK)
            assert 'failed' not in summary, "must not report an ordinary failure"


# --- 4. the stale-claim sweep ----------------------------------------------

def test_a_stale_undelivered_claim_is_cleaned_up_not_re_delivered(
        sessions, two_users):
    """A worker killed mid-tick leaves a claim no handler can ever revisit.

    The sweep is table maintenance, not recovery: it deletes the row so a
    crash-looping deployment cannot accumulate one abandoned claim per user per
    week forever. It does NOT re-deliver that week's note, because by the time
    a claim is this old the user's due window has closed and `due_now` is
    false. The missed note is simply missed; the next week's tick serves the
    user again.
    """
    _claim(sessions, two_users['a'],
           created=TICK - datetime.timedelta(minutes=45))

    with sessions() as s:
        released = weekly_note.release_stale_claims(s, now=TICK)

    assert released == 1
    assert _snapshots(sessions, two_users['a']) == []


def test_a_delivered_claim_is_never_released(sessions, two_users):
    """The user's note arrived; re-notifying them is the bug this avoids."""
    _claim(sessions, two_users['a'], created=TICK - datetime.timedelta(days=7),
           delivered_at=TICK - datetime.timedelta(days=7))

    with sessions() as s:
        assert weekly_note.release_stale_claims(s, now=TICK) == 0
    assert len(_snapshots(sessions, two_users['a'])) == 1


def test_a_fresh_claim_is_left_alone(sessions, two_users):
    """Another worker may be mid-delivery right now."""
    _claim(sessions, two_users['a'],
           created=TICK - datetime.timedelta(minutes=5))
    with sessions() as s:
        assert weekly_note.release_stale_claims(s, now=TICK) == 0
    assert len(_snapshots(sessions, two_users['a'])) == 1


def test_the_sweep_runs_before_the_users_are_processed(
        sessions, monkeypatch, two_users):
    """Otherwise a stranded week is just reported as `already_sent`."""
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    _claim(sessions, two_users['a'],
           created=TICK - datetime.timedelta(minutes=45))

    summary = _run(sessions, monkeypatch, now=TICK)

    assert summary['released_stale'] == 1
    assert summary['sent'] == 1, "the freed week was served in the same tick"
    assert summary['already_sent'] == 0
    assert len(_snapshots(sessions, two_users['a'])) == 1


def test_a_delivered_claim_records_its_delivery(sessions, monkeypatch, two_users):
    """The stamp is what makes the sweep able to tell delivered from stranded.

    It records WHEN the note landed, so it must be the wall clock at the moment
    of the send, not the tick's own instant. `TICK` is a fixed date, so the two
    are distinguishable -- which is what lets this test fail if the tick time is
    ever reused.
    """
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')

    before = datetime.datetime.now(datetime.timezone.utc)
    _run(sessions, monkeypatch, now=TICK)
    after = datetime.datetime.now(datetime.timezone.utc)

    stored = _snapshots(sessions, two_users['a'])[0]
    assert stored.delivered_at is not None
    assert stored.iso_week == '2026-W41'
    assert stored.delivered_at != TICK, \
        'delivered_at must be the send time, not the tick instant'
    assert before <= stored.delivered_at <= after, \
        'delivered_at must be taken between the start and the end of the tick'


def test_a_failed_send_records_no_delivery_time(sessions, monkeypatch, two_users):
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')

    # Not delivered: the claim is released, so there is nothing left to stamp.
    _run(sessions, monkeypatch, now=TICK,
         result=PushResult(attempted=False, reason='unconfigured'))
    assert _snapshots(sessions, two_users['a']) == []

    # Not delivered but the row survived (a concurrent release): unstamped.
    _claim(sessions, two_users['a'], created=TICK)
    with sessions() as s:
        weekly_note.release_stale_claims(
            s, now=TICK - datetime.timedelta(minutes=31))
    assert len(_snapshots(sessions, two_users['a'])) == 1
    assert _snapshots(sessions, two_users['a'])[0].delivered_at is None