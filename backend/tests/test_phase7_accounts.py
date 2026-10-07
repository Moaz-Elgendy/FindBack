import base64
import io
import json
import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from jose import JWTError, jwt
from sqlalchemy import text

from app.services import auth_tokens, identity, retention
from test_phase16_multitenant import admin_engine, db, sessions, client, _make_user, _ready_item


@pytest.fixture(autouse=True)
def auth_settings(monkeypatch):
    monkeypatch.setenv('API_SECRET_KEY', 'test-only-secret-not-a-production-key')
    monkeypatch.delenv('SUPABASE_URL', raising=False)
    monkeypatch.delenv('SUPABASE_JWT_SECRET', raising=False)
    monkeypatch.setenv('DEV_AUTH_ENABLED', 'false')
    auth_tokens._jwks_cache.clear()


@pytest.fixture
def signed_supabase(monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_numbers()
    def encoded(n):
        return base64.urlsafe_b64encode(n.to_bytes(32, 'big')).rstrip(b'=').decode()
    jwk = {'kid': 'key1', 'alg': 'ES256', 'kty': 'EC', 'crv': 'P-256',
           'x': encoded(public.x), 'y': encoded(public.y)}
    calls = []
    def fetch(url, timeout):
        calls.append(url)
        return io.BytesIO(json.dumps({'keys': [jwk]}).encode())
    monkeypatch.setattr(auth_tokens, 'urlopen', fetch)
    monkeypatch.setenv('SUPABASE_URL', 'https://project.supabase.co')
    def sign(**changes):
        claims = {'iss': 'https://project.supabase.co/auth/v1', 'aud': 'authenticated',
                  'exp': int(time.time()) + 600, 'sub': str(uuid.uuid4()), 'email': 'account@example.test'}
        claims.update(changes)
        return jwt.encode(claims, key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()),
                          algorithm='ES256', headers={'kid': 'key1'})
    return sign, calls


def test_supabase_signature_cache_and_strict_claims(signed_supabase):
    sign, calls = signed_supabase
    token = sign()
    assert auth_tokens.decode_access_token(token)['email'] == 'account@example.test'
    auth_tokens.decode_access_token(token)
    assert len(calls) == 1
    for changes in [{'iss': 'https://other/auth/v1'}, {'aud': 'other'}, {'exp': 0},
                    {'sub': ''}, {'sub': 'guest:' + str(uuid.uuid4())}]:
        with pytest.raises(JWTError):
            auth_tokens.decode_access_token(sign(**changes))
    damaged = token.rsplit('.', 1)[0] + '.AAAAAAAA'
    with pytest.raises(JWTError):
        auth_tokens.decode_access_token(damaged)


def test_unknown_key_refresh_network_and_wrong_algorithm(signed_supabase, monkeypatch):
    sign, calls = signed_supabase
    token = sign()
    auth_tokens.decode_access_token(token)
    header, body, sig = token.split('.')
    unknown = base64.urlsafe_b64encode(json.dumps({'alg': 'ES256', 'kid': 'unknown'}).encode()).rstrip(b'=').decode()
    with pytest.raises(JWTError):
        auth_tokens.decode_access_token('.'.join([unknown, body, sig]))
    assert len(calls) == 2
    hs = jwt.encode({'sub': 'account', 'aud': 'authenticated'}, 'test-only-secret-not-a-production-key', algorithm='HS256')
    with pytest.raises(JWTError):
        auth_tokens.decode_access_token(hs)
    auth_tokens._jwks_cache.clear()
    def offline(*args, **kwargs):
        raise OSError('offline')
    monkeypatch.setattr(auth_tokens, 'urlopen', offline)
    with pytest.raises(JWTError):
        auth_tokens.decode_access_token(token)


def test_guests_are_distinct_and_account_me_uses_signed_subject(client, sessions, signed_supabase):
    first = client.request('POST', '/api/v1/auth/guest').json()
    second = client.request('POST', '/api/v1/auth/guest').json()
    assert first['user_id'] != second['user_id']
    def headers(token):
        return {'Authorization': 'Bearer ' + token}
    me = client.request('GET', '/api/v1/auth/me', headers=headers(first['access_token']))
    assert me.status_code == 200
    assert me.json()['is_guest'] and me.json()['id'] == first['user_id']
    item_id, _ = _ready_item(sessions, uuid.UUID(first['user_id']), 'https://example.test/guest')
    assert client.request('GET', '/api/v1/items/' + item_id, headers=headers(second['access_token'])).status_code == 404
    sign, _ = signed_supabase
    account = client.request('GET', '/api/v1/auth/me', headers=headers(sign()))
    assert account.status_code == 200 and not account.json()['is_guest']
    assert client.request('GET', '/api/v1/auth/me').status_code == 401


def test_guest_cannot_claim_legacy_email_or_account_subject(sessions):
    legacy = _make_user(sessions, 'dev@findback.local')
    token, _ = auth_tokens.issue_guest_token()
    claims = auth_tokens.decode_access_token(token)
    claims.update(email='dev@findback.local', app_metadata={'email_verified': True}, user_metadata={'role': 'account'})
    with sessions() as session:
        guest, _ = identity.resolve_user(session, claims)
        assert guest.id != legacy and guest.email != 'dev@findback.local'
    claims['sub'] = 'account-subject'
    forged = jwt.encode(claims, 'test-only-secret-not-a-production-key', algorithm='HS256')
    with pytest.raises(JWTError):
        auth_tokens.decode_access_token(forged)


def test_expired_guest_cleanup_preserves_accounts_shared_refs_and_active_jobs(sessions):
    from app.models import ContentAsset, Item, ProcessingJob, UserMemory
    guest_subject = 'guest:' + str(uuid.uuid4())
    guest = _make_user(sessions, guest_subject + '@findback.local', guest_subject)
    account = _make_user(sessions, 'preserved@example.test', 'account-preserved')
    expired, expired_asset = _ready_item(sessions, guest, 'https://example.test/expired')
    shared, shared_asset = _ready_item(sessions, guest, 'https://example.test/shared')
    active, active_asset = _ready_item(sessions, guest, 'https://example.test/active')
    normal, normal_asset = _ready_item(sessions, account, 'https://example.test/account')
    with sessions() as session:
        session.add(UserMemory(user_id=account, content_id=uuid.UUID(shared_asset)))
        session.add(ProcessingJob(content_id=uuid.UUID(active_asset), status='PROCESSING'))
        session.execute(text("UPDATE items SET created_at=now()-interval '25 hours'"))
        session.commit()
        assert retention.purge_expired_guest_staging(session) == 2
        assert session.get(Item, uuid.UUID(expired)) is None
        assert session.get(ContentAsset, uuid.UUID(expired_asset)) is None
        assert session.get(ContentAsset, uuid.UUID(shared_asset)) is not None
        assert session.get(Item, uuid.UUID(active)) is not None
        assert session.get(Item, uuid.UUID(normal)) is not None
        assert session.get(ContentAsset, uuid.UUID(normal_asset)) is not None
        assert retention.purge_expired_guest_staging(session) == 0


def test_delete_completed_guest_save_removes_only_guest_orphan(sessions):
    from app.models import ContentAsset
    subject = 'guest:' + str(uuid.uuid4())
    guest = _make_user(sessions, subject + '@findback.local', subject)
    item, asset = _ready_item(sessions, guest, 'https://example.test/completed')
    with sessions() as session:
        retention.delete_save(session, guest, uuid.UUID(asset))
        assert session.get(ContentAsset, uuid.UUID(asset)) is None


def test_guest_token_requires_expiry_purpose_and_reserved_subject(monkeypatch):
    token, expires = auth_tokens.issue_guest_token()
    claims = auth_tokens.decode_access_token(token)
    assert 23 * 3600 < expires.timestamp() - time.time() <= 24 * 3600
    for changes in [{'exp': 0}, {'purpose': 'account'}, {'sub': 'account'},
                    {'sub': 'guest:invalid'}, {'aud': 'authenticated'}]:
        changed = {**claims, **changes}
        invalid = jwt.encode(changed, 'test-only-secret-not-a-production-key', algorithm='HS256')
        with pytest.raises(JWTError):
            auth_tokens.decode_access_token(invalid)
    monkeypatch.setenv('GUEST_RETENTION_HOURS', '3')
    _, short = auth_tokens.issue_guest_token()
    assert 2 * 3600 < short.timestamp() - time.time() <= 3 * 3600
    monkeypatch.delenv('API_SECRET_KEY')
    with pytest.raises(JWTError):
        auth_tokens.issue_guest_token()


def test_expired_jwks_cache_refreshes(signed_supabase):
    sign, calls = signed_supabase
    token = sign()
    auth_tokens.decode_access_token(token)
    url = 'https://project.supabase.co'
    _, keys = auth_tokens._jwks_cache[url]
    auth_tokens._jwks_cache[url] = (0, keys)
    auth_tokens.decode_access_token(token)
    assert len(calls) == 2



def test_active_guest_orphan_expires_only_after_worker_finishes(sessions):
    from app.models import ContentAsset, ProcessingJob
    subject = 'guest:' + str(uuid.uuid4())
    guest = _make_user(sessions, subject + '@findback.local', subject)
    _, asset = _ready_item(sessions, guest, 'https://example.test/active-orphan')
    _, public_asset = _ready_item(sessions, guest, 'https://example.test/public-orphan', asset_visibility='PUBLIC')
    account = _make_user(sessions, 'orphan-account@example.test', 'orphan-account')
    _, account_asset = _ready_item(sessions, account, 'https://example.test/account-orphan')
    with sessions() as session:
        session.add(ProcessingJob(content_id=uuid.UUID(asset), status='PROCESSING'))
        session.execute(text("UPDATE content_assets SET created_at=now()-interval '25 hours'"))
        session.commit()
        retention.delete_save(session, guest, uuid.UUID(asset))
        retention.delete_save(session, account, uuid.UUID(account_asset))
        retention.delete_save(session, guest, uuid.UUID(public_asset))
        assert session.get(ContentAsset, uuid.UUID(asset)) is not None
        retention.purge_expired_guest_staging(session)
        assert session.get(ContentAsset, uuid.UUID(asset)) is not None
        session.execute(text("UPDATE processing_jobs SET status='READY', locked_at=now() WHERE content_id=:id"), {'id': asset})
        session.commit()
        retention.purge_expired_guest_staging(session)
        assert session.get(ContentAsset, uuid.UUID(asset)) is not None
        session.execute(text("UPDATE processing_jobs SET locked_at=NULL WHERE content_id=:id"), {'id': asset})
        session.commit()
        retention.purge_expired_guest_staging(session)
        assert session.get(ContentAsset, uuid.UUID(asset)) is None
        assert session.get(ContentAsset, uuid.UUID(account_asset)) is not None
        assert session.get(ContentAsset, uuid.UUID(public_asset)) is not None
