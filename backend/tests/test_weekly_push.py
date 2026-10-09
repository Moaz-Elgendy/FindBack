"""The weekly note's delivery: generic text, private payload, dead tokens dropped.

Three properties matter more than the request being sent at all:

  * the lock screen never carries a memory title -- only a count, a fixed body,
    and the id the app fetches the list from;
  * the data payload carries ONLY `type` and `snapshot_id`, because a payload
    is logged, cached and visible in developer tooling;
  * a token FCM reports as permanently dead is deleted rather than retried on
    every future note.

Nothing here reaches the network: the httpx transport is mocked, and the
service-account credential is faked rather than read from disk.
"""
import datetime
import json

import httpx
import pytest

from app.services import weekly_note
from app.services.weekly_note import (WEEKLY_NOTE_BODY, PushResult,
                                      send_weekly_push)
from test_phase16_multitenant import admin_engine, db, sessions, two_users

ANDROID = 'fcm-token-android-0001'
IOS = 'apns-token-ios-0001'
SECRET_TITLE = 'How to build a sourdough starter'


def _service_account(email='firebase-adminsdk-abc123@findback-test.iam.gserviceaccount.com'):
    """Just enough of a credential for the code under test."""
    class _Credentials:
        service_account_email = email

        def __init__(self):
            self.token = 'test-access-token'
            self.valid = True

        def refresh(self, request):
            self.token = 'refreshed-access-token'
            self.valid = True
    return _Credentials()


class _Recorder:
    """Captures every request and replies with a canned status.

    Wrapped in a real `httpx.MockTransport`, the same seam `ai.py` exposes, so
    no test reaches the network.
    """

    def __init__(self, replies=None, default=200):
        self.requests = []
        self.replies = replies or {}
        self.default = default

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        token = json.loads(request.content)['message']['token']
        status, body = self.replies.get(token, (self.default, {}))
        return httpx.Response(status, json=body, request=request)

    @property
    def transport(self):
        return httpx.MockTransport(self.handler)

    @property
    def bodies(self):
        return [json.loads(r.content)['message'] for r in self.requests]


@pytest.fixture
def push(monkeypatch, sessions):
    """Wire `send_weekly_push` to a fake credential and a recording transport."""
    monkeypatch.setenv('FCM_SERVICE_ACCOUNT_FILE', '/tmp/fake-service-account.json')
    monkeypatch.setattr(weekly_note, '_CREDENTIALS', _service_account())
    monkeypatch.setattr(weekly_note, 'SessionLocal', sessions)

    def build(replies=None, default=200):
        recorder = _Recorder(replies=replies, default=default)
        monkeypatch.setattr(weekly_note, '_TRANSPORT', recorder.transport)
        return recorder

    return build


def _register(sessions, user_id, token, platform):
    from app.models import DeviceToken
    with sessions() as s:
        s.add(DeviceToken(user_id=user_id, token=token, platform=platform))
        s.commit()


def _tokens(sessions):
    from app.models import DeviceToken
    with sessions() as s:
        return sorted(row[0] for row in s.query(DeviceToken.token).all())


def _edit_title(sessions, item_id):
    """A memory title that must not appear anywhere in what we send."""
    from sqlalchemy import text
    with sessions() as s:
        s.execute(text("UPDATE items SET edited_title = :t, title = :t, "
                       "title_clean = :t WHERE id = :i"),
                  {'t': SECRET_TITLE, 'i': item_id})
        s.commit()


# --- 1. wording -------------------------------------------------------------

def test_one_save_uses_the_singular(push, sessions, two_users):
    _register(sessions, two_users['a'], ANDROID, 'android')
    transport = push()
    send_weekly_push(two_users['a'], 'snap-1', 1)
    assert transport.bodies[0]['notification']['title'] == '1 thing you saved and forgot'


@pytest.mark.parametrize('count', [2, 3, 7, 100])
def test_many_saves_use_the_plural(push, sessions, two_users, count):
    _register(sessions, two_users['a'], ANDROID, 'android')
    transport = push()
    send_weekly_push(two_users['a'], 'snap-1', count)
    assert transport.bodies[0]['notification']['title'] == f'{count} things you saved and forgot'


def test_the_body_is_the_fixed_sentence_every_time(push, sessions, two_users):
    _register(sessions, two_users['a'], ANDROID, 'android')
    transport = push()
    for count in (1, 5):
        send_weekly_push(two_users['a'], 'snap-1', count)
    assert [b['notification']['body'] for b in transport.bodies] == \
        [WEEKLY_NOTE_BODY] * 2
    assert WEEKLY_NOTE_BODY == 'Open FindBack to take another look.'


# --- 2. no titles, anywhere -------------------------------------------------

def test_the_request_carries_no_memory_title(push, sessions, two_users):
    """The whole serialized request, not just the fields we think about."""
    _register(sessions, two_users['a'], ANDROID, 'android')
    _register(sessions, two_users['a'], IOS, 'ios')
    _edit_title(sessions, two_users['a_item'])
    transport = push()

    send_weekly_push(two_users['a'], 'snap-abc', 2)

    assert transport.requests, "nothing was sent"
    for request in transport.requests:
        raw = request.content.decode('utf-8')
        assert SECRET_TITLE not in raw, "a memory title reached the push request"
        for field in ('url', 'summary', 'canonical_url', 'source_domain'):
            assert field not in raw, f"{field} is private and must not be sent"


def test_the_data_payload_holds_only_the_three_navigation_keys(
        push, sessions, two_users):
    """type, snapshot_id and account_id -- and nothing else.

    `account_id` was added so the app can refuse a note addressed to an account
    other than the signed-in one, which is the client half of the token-moves-
    between-accounts guard in migration 0023. It carries no memory content.
    """
    _register(sessions, two_users['a'], ANDROID, 'android')
    transport = push()
    send_weekly_push(two_users['a'], 'snap-xyz', 4)
    data = transport.bodies[0]['data']
    assert data == {'type': 'weekly_note', 'snapshot_id': 'snap-xyz',
                    'account_id': str(two_users['a'])}


def test_the_account_id_is_the_owner_not_the_device_holder(
        push, sessions, two_users):
    """The id travels with the note, so the app can match it to its own account."""
    _register(sessions, two_users['a'], ANDROID, 'android')
    transport = push()
    send_weekly_push(two_users['a'], 'snap-xyz', 1)
    assert transport.bodies[0]['data']['account_id'] == str(two_users['a'])


def test_android_visibility_is_private(push, sessions, two_users):
    _register(sessions, two_users['a'], ANDROID, 'android')
    transport = push()
    send_weekly_push(two_users['a'], 'snap-1', 1)
    assert transport.bodies[0]['android']['notification'][
        'visibility'] == 'PRIVATE'


def test_every_registered_device_is_sent_to(push, sessions, two_users):
    _register(sessions, two_users['a'], ANDROID, 'android')
    _register(sessions, two_users['a'], IOS, 'ios')
    transport = push()
    send_weekly_push(two_users['a'], 'snap-1', 1)
    assert sorted(b['token'] for b in transport.bodies) == sorted([ANDROID, IOS])
    assert transport.bodies[0]['data']['snapshot_id'] == 'snap-1'


def test_only_this_users_devices_are_sent_to(push, sessions, two_users):
    _register(sessions, two_users['a'], ANDROID, 'android')
    _register(sessions, two_users['b'], IOS, 'ios')
    transport = push()
    send_weekly_push(two_users['a'], 'snap-1', 1)
    assert [b['token'] for b in transport.bodies] == [ANDROID]


def test_the_request_is_authorized_and_addressed(push, sessions, two_users):
    _register(sessions, two_users['a'], ANDROID, 'android')
    transport = push()
    send_weekly_push(two_users['a'], 'snap-1', 1)
    request = transport.requests[0]
    assert request.headers['authorization'] == 'Bearer test-access-token'
    # The project is taken from the service-account address, so the URL is the
    # one Firebase expects for this key.
    assert request.url.path == '/v1/projects/findback-test/messages:send'


# --- 3. dead tokens are removed --------------------------------------------

def test_an_unregistered_token_is_deleted(push, sessions, two_users):
    _register(sessions, two_users['a'], ANDROID, 'android')
    transport = push(replies={ANDROID: (404, {'error': {'status': 'UNREGISTERED'}})})
    result = send_weekly_push(two_users['a'], 'snap-1', 1)
    assert (result.sent, result.removed) == (0, 1)
    assert _tokens(sessions) == []


def test_an_invalid_argument_token_is_deleted(push, sessions, two_users):
    _register(sessions, two_users['a'], ANDROID, 'android')
    transport = push(replies={ANDROID: (400, {'error': {'status': 'INVALID_ARGUMENT', 'details': [{'@type': 'type.googleapis.com/google.firebase.fcm.v1.FcmError', 'errorCode': 'INVALID_ARGUMENT'}]}})})
    send_weekly_push(two_users['a'], 'snap-1', 1)
    assert _tokens(sessions) == []


def test_a_transient_failure_keeps_the_token(push, sessions, two_users):
    """A quota or 5xx says nothing about the token; deleting it would be wrong."""
    _register(sessions, two_users['a'], ANDROID, 'android')
    transport = push(replies={ANDROID: (503, {'error': {'status': 'UNAVAILABLE'}})})
    result = send_weekly_push(two_users['a'], 'snap-1', 1)
    assert (result.sent, result.removed, result.failed) == (0, 0, 1)
    assert _tokens(sessions) == [ANDROID]


def test_an_unparseable_error_body_does_not_delete_the_token(monkeypatch, push, sessions, two_users):
    """A 500 with an HTML body must not be mistaken for a dead token."""
    _register(sessions, two_users['a'], ANDROID, 'android')
    push()

    def handler(request):
        return httpx.Response(500, content=b'<html>gateway</html>', request=request)

    monkeypatch.setattr(weekly_note, '_TRANSPORT', httpx.MockTransport(handler))
    send_weekly_push(two_users['a'], 'snap-1', 1)
    assert _tokens(sessions) == [ANDROID]


def test_one_dead_device_does_not_stop_the_others(push, sessions, two_users):
    _register(sessions, two_users['a'], ANDROID, 'android')
    _register(sessions, two_users['a'], IOS, 'ios')
    transport = push(replies={ANDROID: (404, {'error': {'status': 'UNREGISTERED'}})})

    result = send_weekly_push(two_users['a'], 'snap-1', 1)
    assert (result.sent, result.removed) == (1, 1)
    assert len(transport.requests) == 2, "the second device was never tried"
    assert _tokens(sessions) == [IOS]


# --- 4. nothing configured, nothing to send ---------------------------------

def test_without_the_service_account_path_nothing_is_sent(monkeypatch, sessions, two_users):
    """The documented behaviour when push is not configured: warn, do nothing."""
    monkeypatch.delenv('FCM_SERVICE_ACCOUNT_FILE', raising=False)
    monkeypatch.setattr(weekly_note, '_TRANSPORT', httpx.MockTransport(
        lambda request: pytest.fail("an unconfigured deployment must not call FCM")))
    result = send_weekly_push(two_users['a'], 'snap-1', 3)
    assert result == PushResult(attempted=False, reason='unconfigured')
    assert result.delivered is False


def test_an_unset_variable_is_reported_not_raised(monkeypatch, sessions, two_users, caplog):
    import logging
    monkeypatch.delenv('FCM_SERVICE_ACCOUNT_FILE', raising=False)
    with caplog.at_level(logging.WARNING, logger='findback.weekly_note'):
        send_weekly_push(two_users['a'], 'snap-1', 3)
    assert any('FCM_SERVICE_ACCOUNT_FILE' in r.message for r in caplog.records)


def test_a_user_with_no_devices_sends_nothing(push, sessions, two_users, monkeypatch):
    monkeypatch.setattr(weekly_note, '_TRANSPORT', httpx.MockTransport(
        lambda request: pytest.fail("no device means no request")))
    result = send_weekly_push(two_users['a'], 'snap-1', 1)
    assert (result.attempted, result.reason) == (False, 'no_devices')


def test_unusable_credentials_send_nothing_rather_than_raising(monkeypatch, sessions, two_users):
    """A bad key file must not take the whole weekly tick down."""
    monkeypatch.setenv('FCM_SERVICE_ACCOUNT_FILE', '/nonexistent/key.json')
    monkeypatch.setattr(weekly_note, 'SessionLocal', sessions)
    # Cleared, so the real google.auth load is attempted and fails for real --
    # this is the one test that exercises that path against the real library.
    monkeypatch.setattr(weekly_note, '_CREDENTIALS', None)
    monkeypatch.setattr(weekly_note, '_TRANSPORT', httpx.MockTransport(
        lambda request: pytest.fail("no credential means no request")))
    result = send_weekly_push(two_users['a'], 'snap-1', 1)
    assert (result.attempted, result.reason) == (False, 'credentials_unusable')


def test_a_real_service_account_key_is_loaded_by_google_auth(monkeypatch, tmp_path, sessions, two_users):
    """The google-auth integration itself, not just our test seam.

    A genuine RSA service-account key is generated on disk and loaded through
    the real `from_service_account_file`, so the part most likely to be wrong
    -- reading the file, parsing the PEM, and deriving the project from the
    client address -- is actually executed.

    Only the OAuth exchange with Google is stubbed, because this suite never
    touches the network.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from google.oauth2 import service_account

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()).decode()
    path = tmp_path / 'sa.json'
    path.write_text(json.dumps({
        'type': 'service_account', 'project_id': 'findback-test',
        'private_key_id': 'k1', 'private_key': private_pem,
        'client_email': 'firebase-adminsdk-abc@findback-test.iam.gserviceaccount.com',
        'client_id': '1', 'token_uri': 'https://oauth2.googleapis.com/token'}))

    def fake_refresh(self, request):
        self.token = 'token-from-google-auth'
        self.expiry = datetime.datetime.max.replace(tzinfo=datetime.timezone.utc)

    monkeypatch.setattr(service_account.Credentials, 'refresh', fake_refresh)
    monkeypatch.setenv('FCM_SERVICE_ACCOUNT_FILE', str(path))
    monkeypatch.setattr(weekly_note, 'SessionLocal', sessions)
    monkeypatch.setattr(weekly_note, '_CREDENTIALS', None)

    _register(sessions, two_users['a'], ANDROID, 'android')
    recorder = _Recorder()
    monkeypatch.setattr(weekly_note, '_TRANSPORT', recorder.transport)

    send_weekly_push(two_users['a'], 'snap-1', 2)
    assert len(recorder.requests) == 1
    assert recorder.requests[0].headers['authorization'] == 'Bearer token-from-google-auth'
    # The project comes from the client address in the real loaded key.
    assert recorder.requests[0].url.path == '/v1/projects/findback-test/messages:send'