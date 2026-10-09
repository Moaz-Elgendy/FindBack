"""Transport failures, and one broken account not costing the others their note.

The bug these lock down: the httpx call was unguarded, so a `ConnectError` or a
timeout -- the single most likely real failure, FCM being unreachable -- escaped
`send_weekly_push`. That skipped the claim release, so the account was silenced
for the rest of the week, and it aborted the loop in `run_weekly_note`, so every
user after this one lost their note too.

Two rules therefore hold:

  * `send_weekly_push` always answers with a `PushResult`, never an exception,
    for anything that can go wrong talking to FCM;
  * `run_weekly_note` isolates each account, releasing that account's claim and
    moving to the next one.

The exception MESSAGE is never asserted or logged. A transport error can carry
the request URL and the token, and the token is a live credential, so only the
type reaches the log.
"""
import datetime

import httpx
from sqlalchemy import text

from app.models import DeviceToken
from app.services import weekly_note
from app.services.weekly_note import PushResult, run_weekly_note
from test_phase16_multitenant import admin_engine, db, sessions, two_users, _ready_item
from test_weekly_note import NOW, _forgotten, _prefer

TICK = datetime.datetime(2026, 10, 8, 18, 0, tzinfo=datetime.timezone.utc)


def _device(sessions, user_id, token, platform='android'):
    with sessions() as s:
        s.add(DeviceToken(user_id=user_id, token=token, platform=platform))
        s.commit()


def _snapshots(sessions, user_id):
    with sessions() as s:
        return [str(row[0]) for row in s.query(
            weekly_note.WeeklySnapshot.id)
            .filter(weekly_note.WeeklySnapshot.user_id == user_id).all()]


class _Credentials:
    service_account_email = 'sa@findback-test.iam.gserviceaccount.com'

    def __init__(self):
        self.token = 'access-token'
        self.valid = True

    def refresh(self, request):
        pass


def _wired(monkeypatch, sessions):
    """A fully wired send path with a transport the test controls."""
    monkeypatch.setenv('FCM_SERVICE_ACCOUNT_FILE', '/tmp/fake-sa.json')
    monkeypatch.setattr(weekly_note, '_CREDENTIALS', _Credentials())
    monkeypatch.setattr(weekly_note, 'SessionLocal', sessions)


def _raising(monkeypatch, error, for_tokens=None):
    """A transport that raises `error` instead of answering.

    `for_tokens` limits the failure to particular devices; anything else is
    answered normally, which is how a mixed run is built.
    """
    def handler(request):
        token = request.content.decode()
        if for_tokens is None or any(name in token for name in for_tokens):
            raise error
        return httpx.Response(200, json={}, request=request)

    monkeypatch.setattr(weekly_note, '_TRANSPORT', httpx.MockTransport(handler))


# --- 1. send_weekly_push always answers ------------------------------------

def test_a_connect_error_returns_a_result_instead_of_raising(
        monkeypatch, sessions, two_users):
    _wired(monkeypatch, sessions)
    _device(sessions, two_users['a'], 'device-1')
    _raising(monkeypatch, httpx.ConnectError('connection refused'))

    result = weekly_note.send_weekly_push(two_users['a'], 'snap-1', 3)

    assert isinstance(result, PushResult)
    assert result.attempted is True
    assert (result.sent, result.failed) == (0, 1)
    assert result.delivered is False
    assert result.reason == 'not_delivered'


def test_a_timeout_returns_a_result_instead_of_raising(
        monkeypatch, sessions, two_users):
    _wired(monkeypatch, sessions)
    _device(sessions, two_users['a'], 'device-1')
    _raising(monkeypatch, httpx.ReadTimeout('timed out'))

    result = weekly_note.send_weekly_push(two_users['a'], 'snap-1', 1)
    assert (result.sent, result.failed) == (0, 1)


def test_a_write_timeout_is_also_caught(monkeypatch, sessions, two_users):
    """`TimeoutException` covers both connect and read timeouts."""
    _wired(monkeypatch, sessions)
    _device(sessions, two_users['a'], 'device-1')
    _raising(monkeypatch, httpx.WriteTimeout('write timed out'))
    assert weekly_note.send_weekly_push(
        two_users['a'], 'snap-1', 1).failed == 1


def test_the_transport_failure_is_logged_by_type_and_never_by_message(
        monkeypatch, sessions, two_users, caplog):
    """The message can carry the token; the log must not."""
    import logging
    _wired(monkeypatch, sessions)
    _device(sessions, two_users['a'], 'device-1')
    secret = 'device-token-must-not-be-logged'
    _raising(monkeypatch, httpx.ConnectError(f'failed to reach {secret}'))

    with caplog.at_level(logging.WARNING, logger='findback.weekly_note'):
        weekly_note.send_weekly_push(two_users['a'], 'snap-1', 1)

    messages = '\n'.join(record.getMessage() for record in caplog.records)
    assert secret not in messages, "the token reached a shipped log"
    assert 'ConnectError' in messages, "the type should still be reported"


# --- 2. a failing send releases the week's claim ----------------------------

def _run(sessions, monkeypatch, now=TICK):
    with sessions() as s:
        return run_weekly_note(s, now=now)


def test_a_connect_error_releases_the_claim(sessions, monkeypatch, two_users):
    _wired(monkeypatch, sessions)
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
                   created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    _device(sessions, two_users['a'], 'device-1')
    _raising(monkeypatch, httpx.ConnectError('connection refused'))

    summary = _run(sessions, monkeypatch)

    assert summary['retry'] == 1 and summary['sent'] == 0
    assert _snapshots(sessions, two_users['a']) == [], \
        'the account is silenced for the week over a transport error'


def test_a_timeout_releases_the_claim(sessions, monkeypatch, two_users):
    _wired(monkeypatch, sessions)
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
                   created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    _device(sessions, two_users['a'], 'device-1')
    _raising(monkeypatch, httpx.ReadTimeout('timed out'))

    summary = _run(sessions, monkeypatch)

    assert summary['retry'] == 1
    assert _snapshots(sessions, two_users['a']) == []


def test_a_released_claim_can_still_be_won_after_the_network_recovers(
        sessions, monkeypatch, two_users):
    _wired(monkeypatch, sessions)
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
                   created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    _device(sessions, two_users['a'], 'device-1')

    _raising(monkeypatch, httpx.ConnectError('down'))
    assert _run(sessions, monkeypatch)['retry'] == 1

    # The network comes back inside the same window.
    _raising(monkeypatch, httpx.ConnectError('down'), for_tokens=[])
    assert _run(sessions, monkeypatch)['sent'] == 1
    assert len(_snapshots(sessions, two_users['a'])) == 1


# --- 3. one account never costs the others ---------------------------------

def test_one_users_exception_does_not_stop_the_next(
        sessions, monkeypatch, two_users):
    """The regression that mattered: a raise aborted the whole loop."""
    _wired(monkeypatch, sessions)
    for key in ('a', 'b'):
        _forgotten(sessions, two_users[key], f'https://old.test/{key}',
                   created=NOW - datetime.timedelta(days=30))
        _prefer(sessions, two_users[key], enabled=True, weekday=3, hour=18,
                time_zone='UTC')
        _device(sessions, two_users[key], f'device-{key}')
    sent = []

    def explode_for_a(user_id, snapshot_id, count):
        sent.append(str(user_id))
        if str(user_id) == str(two_users['a']):
            raise RuntimeError('something went wrong for this user')
        return PushResult(attempted=True, sent=1)

    monkeypatch.setattr(weekly_note, 'send_weekly_push', explode_for_a)

    summary = _run(sessions, monkeypatch)

    assert summary['failed'] == 1, "one account failed"
    assert summary['sent'] == 1, "the next account was still notified"
    assert sent == [str(two_users['a']), str(two_users['b'])]


def test_the_failing_users_claim_is_released(sessions, monkeypatch, two_users):
    """Otherwise the exception silences that account for the whole week."""
    _wired(monkeypatch, sessions)
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
                   created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')

    def explode(user_id, snapshot_id, count):
        raise RuntimeError('boom')

    monkeypatch.setattr(weekly_note, 'send_weekly_push', explode)

    assert _run(sessions, monkeypatch)['failed'] == 1
    assert _snapshots(sessions, two_users['a']) == [], \
        'a raised exception must not leave the week claimed'


def test_the_exception_is_logged_by_user_and_type_only(
        sessions, monkeypatch, two_users, caplog):
    import logging
    _wired(monkeypatch, sessions)
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
                   created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')

    def explode(user_id, snapshot_id, count):
        raise RuntimeError('the secret title of a private memory')

    monkeypatch.setattr(weekly_note, 'send_weekly_push', explode)

    with caplog.at_level(logging.ERROR, logger='findback.weekly_note'):
        _run(sessions, monkeypatch)

    messages = '\n'.join(record.getMessage() for record in caplog.records)
    assert 'the secret title of a private memory' not in messages
    assert str(two_users['a']) in messages, "the operator needs to know whose"
    assert 'RuntimeError' in messages


# --- 4. a mixed run keeps the claim ----------------------------------------

def test_one_failing_device_and_one_successful_device_keeps_the_claim(
        sessions, monkeypatch, two_users):
    """Partial delivery is delivery: this user heard the note."""
    _wired(monkeypatch, sessions)
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
                   created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    _device(sessions, two_users['a'], 'device-broken')
    _device(sessions, two_users['a'], 'device-good', platform='ios')
    # Only the first device's transport fails.
    _raising(monkeypatch, httpx.ConnectError('one device offline'),
             for_tokens=['device-broken'])

    summary = _run(sessions, monkeypatch)

    assert summary['sent'] == 1
    assert summary['retry'] == 0
    assert len(_snapshots(sessions, two_users['a'])) == 1, \
        'a delivered note must keep its claim, or the user is notified again'


def test_every_device_failing_releases_the_claim(sessions, monkeypatch, two_users):
    _wired(monkeypatch, sessions)
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
                   created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    _device(sessions, two_users['a'], 'device-1')
    _device(sessions, two_users['a'], 'device-2', platform='ios')
    _raising(monkeypatch, httpx.ConnectError('the whole network is down'))

    summary = _run(sessions, monkeypatch)
    assert summary['retry'] == 1
    assert _snapshots(sessions, two_users['a']) == []


def test_a_dead_token_is_still_removed_alongside_a_transport_error(
        sessions, monkeypatch, two_users):
    """The two per-device paths must not interfere with each other."""
    _wired(monkeypatch, sessions)
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
                   created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    _device(sessions, two_users['a'], 'device-dead')
    _device(sessions, two_users['a'], 'device-good', platform='ios')

    def handler(request):
        if b'device-dead' in request.content:
            return httpx.Response(404, json={'error': {'status': 'UNREGISTERED'}},
                                  request=request)
        if b'device-good' in request.content:
            raise httpx.ConnectError('offline')
        return httpx.Response(200, json={}, request=request)

    monkeypatch.setattr(weekly_note, '_TRANSPORT', httpx.MockTransport(handler))

    summary = _run(sessions, monkeypatch)
    assert summary['retry'] == 1, "nothing was delivered"
    with sessions() as s:
        remaining = [row[0] for row in s.query(DeviceToken.token).all()]
    assert remaining == ['device-good'], "the dead token should still be gone"