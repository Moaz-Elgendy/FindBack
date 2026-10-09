"""Where the Firebase project comes from, and what happens when it cannot.

The project used to fall back to a hard-coded name. That is the worst possible
default for a send path: a service account pointed at the wrong project would
either deliver into somebody else's Firebase project or fail with a 404 that
reads like a credential problem, and neither is visible in a log line.

So there is no default. `FCM_PROJECT_ID` wins, then the project named by the
service account's own client address, and if neither resolves the tick treats
FCM as not configured: nothing is attempted, an error is logged, and the caller
releases the week's claim rather than posting into an unknown project.
"""
import httpx
import pytest

from app.services import weekly_note
from app.services.weekly_note import PushResult, send_weekly_push
from test_phase16_multitenant import admin_engine, db, sessions, two_users
from test_weekly_push import ANDROID, IOS, _service_account


def _wire(monkeypatch, sessions, email='sa@findback-test.iam.gserviceaccount.com',
          responses=200):
    monkeypatch.setenv('FCM_SERVICE_ACCOUNT_FILE', '/tmp/sa.json')
    monkeypatch.delenv('FCM_PROJECT_ID', raising=False)
    monkeypatch.setattr(weekly_note, '_CREDENTIALS', _service_account(email))
    monkeypatch.setattr(weekly_note, 'SessionLocal', sessions)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(responses, json={}, request=request)

    monkeypatch.setattr(weekly_note, '_TRANSPORT', httpx.MockTransport(handler))
    return seen


def _device(sessions, user_id, token=ANDROID):
    from app.models import DeviceToken
    with sessions() as s:
        s.add(DeviceToken(user_id=user_id, token=token, platform='android'))
        s.commit()


# --- 1. resolution ----------------------------------------------------------

def test_the_project_comes_from_the_service_account_address(monkeypatch):
    monkeypatch.delenv('FCM_PROJECT_ID', raising=False)
    monkeypatch.setattr(
        weekly_note, '_CREDENTIALS',
        _service_account('firebase-adminsdk-x@findback-test.iam.gserviceaccount.com'))
    assert weekly_note._project_id() == 'findback-test'


def test_the_explicit_override_wins(monkeypatch):
    monkeypatch.setenv('FCM_PROJECT_ID', 'chosen-project')
    monkeypatch.setattr(
        weekly_note, '_CREDENTIALS',
        _service_account('sa@from-the-key.iam.gserviceaccount.com'))
    assert weekly_note._project_id() == 'chosen-project'


def test_no_hard_coded_project_remains(monkeypatch):
    """The regression: a default would send into an unknown project."""
    monkeypatch.delenv('FCM_PROJECT_ID', raising=False)
    monkeypatch.setattr(weekly_note, '_CREDENTIALS', _service_account(''))
    assert weekly_note._project_id() is None


def test_an_unparsable_address_resolves_to_nothing(monkeypatch):
    monkeypatch.delenv('FCM_PROJECT_ID', raising=False)
    for email in ('', 'not-an-email', '@', 'sa@'):
        monkeypatch.setattr(weekly_note, '_CREDENTIALS', _service_account(email))
        assert weekly_note._project_id() is None, email


# --- 2. unresolvable project means "not configured" ------------------------

def test_an_unresolvable_project_sends_nothing(monkeypatch, sessions, two_users):
    _wire(monkeypatch, sessions, email='')
    _device(sessions, two_users['a'])

    result = send_weekly_push(two_users['a'], 'snap-1', 3)

    assert result == PushResult(attempted=False, reason='project_unknown')
    assert result.delivered is False


def test_an_unresolvable_project_never_reaches_the_network(
        monkeypatch, sessions, two_users):
    seen = _wire(monkeypatch, sessions, email='')
    _device(sessions, two_users['a'])

    send_weekly_push(two_users['a'], 'snap-1', 1)

    assert seen == [], "nothing may be posted without a known project"


def test_an_unresolvable_project_is_logged_as_an_error(
        monkeypatch, sessions, two_users, caplog):
    import logging
    _wire(monkeypatch, sessions, email='')
    _device(sessions, two_users['a'])

    with caplog.at_level(logging.ERROR, logger='findback.weekly_note'):
        send_weekly_push(two_users['a'], 'snap-1', 1)

    messages = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'FCM_PROJECT_ID' in messages, "the operator is told how to fix it"


def test_a_resolvable_project_still_sends(monkeypatch, sessions, two_users):
    seen = _wire(monkeypatch, sessions)
    _device(sessions, two_users['a'])

    result = send_weekly_push(two_users['a'], 'snap-1', 2)

    assert result.delivered is True
    assert len(seen) == 1
    assert seen[0].url.path == '/v1/projects/findback-test/messages:send'


def test_the_url_uses_the_override_when_one_is_set(monkeypatch, sessions, two_users):
    seen = _wire(monkeypatch, sessions)
    _device(sessions, two_users['a'])
    monkeypatch.setenv('FCM_PROJECT_ID', 'other-project')

    send_weekly_push(two_users['a'], 'snap-1', 1)

    assert seen[0].url.path == '/v1/projects/other-project/messages:send'


# --- 3. end to end: the claim is released ----------------------------------

def test_the_week_claim_is_released_when_the_project_is_unknown(
        sessions, monkeypatch, two_users):
    import datetime
    from app.models import WeeklySnapshot
    from app.services.weekly_note import run_weekly_note
    from test_weekly_note import NOW, _forgotten, _prefer

    _wire(monkeypatch, sessions, email='')
    _forgotten(sessions, two_users['a'], 'https://old.test/a',
               created=NOW - datetime.timedelta(days=30))
    _prefer(sessions, two_users['a'], enabled=True, weekday=3, hour=18,
            time_zone='UTC')
    _device(sessions, two_users['a'])
    tick = datetime.datetime(2026, 10, 8, 18, 0, tzinfo=datetime.timezone.utc)

    with sessions() as s:
        summary = run_weekly_note(s, now=tick)

    assert summary['retry'] == 1, "nothing was delivered"
    with sessions() as s:
        assert s.query(WeeklySnapshot).filter(
            WeeklySnapshot.user_id == two_users['a']).count() == 0, \
            'a week must not be claimed for a note that was never sent'