"""Account contracts against real owner-scoped data and migrations."""
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from test_phase16_multitenant import admin_engine, db, sessions, client, two_users


def test_weekly_preferences_are_validated_persisted_and_private(client, two_users):
    path = '/api/v1/account/weekly-note'
    client.as_user(two_users['a'])
    assert client.request('GET', path).json() == {
        'enabled': False, 'weekday': 6, 'hour': 18, 'minute': 0, 'time_zone': 'UTC'}
    value = {'enabled': True, 'weekday': 0, 'hour': 9, 'minute': 30, 'time_zone': 'Africa/Cairo'}
    assert client.request('PUT', path, json=value).json() == value
    assert client.request('GET', path).json() == value
    for field, invalid in [('weekday', 7), ('hour', 24), ('minute', -1), ('time_zone', 'invented/zone')]:
        assert client.request('PUT', path, json={**value, field: invalid}).status_code == 422
    client.as_user(two_users['b'])
    assert client.request('GET', path).json()['enabled'] is False


def test_export_contains_own_edits_and_notes_but_no_other_users_or_raw_text(client, sessions, two_users):
    with sessions() as session:
        session.execute(text("UPDATE items SET edited_title='My title', edited_summary='My brief', raw_text='secret raw' WHERE id=:id"), {'id': two_users['a_item']})
        session.commit()
    client.as_user(two_users['a'])
    response = client.request('GET', '/api/v1/account/export')
    assert response.status_code == 200
    data = response.json()
    assert data['version'] == 1
    assert len(data['saves']) == 1
    assert data['saves'][0]['title'] == 'My title'
    assert data['saves'][0]['summary'] == 'My brief'
    assert data['saves'][0]['note'] == 'A private note'
    assert 'secret raw' not in response.text
    assert two_users['b_item'] not in response.text
    client.request('DELETE', f"/api/v1/items/{two_users['a_item']}?undoable=true")
    assert client.request('GET', '/api/v1/account/export').json()['saves'] == []


def test_delete_preserves_other_user_and_shared_content_blocks_old_token(client, sessions, two_users, monkeypatch):
    from app.services import account, identity
    from app.models import User, DeletedIdentity
    calls = []
    monkeypatch.setattr(account, 'delete_provider_identity', lambda user: calls.append(user.auth_subject))
    monkeypatch.setattr(account, 'delete_raw_snapshots', lambda ids: calls.append(list(ids)))
    with sessions() as session:
        user = session.get(User, two_users['a'])
        subject = user.auth_subject
        session.execute(text("UPDATE content_assets SET owner_user_id=:u WHERE id=:id"), {'u': two_users['a'], 'id': two_users['b_asset']})
        session.execute(text("UPDATE content_assets SET visibility='PUBLIC' WHERE id=:id"), {'id': two_users['b_asset']})
        session.commit()
    client.as_user(two_users['a'])
    assert client.request('DELETE', '/api/v1/account').status_code == 204
    with sessions() as session:
        assert session.get(User, two_users['a']) is None
        assert session.get(User, two_users['b']) is not None
        assert session.execute(text('SELECT count(*) FROM content_assets WHERE id=:id'), {'id': two_users['b_asset']}).scalar() == 1
        assert session.execute(text('SELECT count(*) FROM content_assets WHERE id=:id'), {'id': two_users['a_asset']}).scalar() == 0
        assert session.query(DeletedIdentity).count() == 1
        with pytest.raises(HTTPException) as failure:
            identity.resolve_user(session, {'sub': subject, 'email': 'a@example.test'})
        assert failure.value.status_code == 401
    assert calls[0] == [two_users['a_item']]
    assert calls[1] == subject
    client.as_user(two_users['b'])
    assert client.request('GET', f"/api/v1/items/{two_users['b_item']}").status_code == 200


def test_delete_provider_failure_keeps_saves_and_preferences(client, sessions, two_users, monkeypatch):
    from app.services import account
    def fail(user):
        raise HTTPException(503, 'Account deletion is temporarily unavailable')
    monkeypatch.setattr(account, 'delete_provider_identity', fail)
    client.as_user(two_users['a'])
    assert client.request('DELETE', '/api/v1/account').status_code == 503
    assert client.request('GET', f"/api/v1/items/{two_users['a_item']}").status_code == 200


def test_unconfigured_deletion_fails_closed_and_never_contacts_wrong_provider(monkeypatch):
    from app.services.account import delete_provider_identity
    from app.models import User
    import uuid
    monkeypatch.setenv('SUPABASE_URL', 'https://project.supabase.co')
    monkeypatch.delenv('SUPABASE_SERVICE_ROLE_KEY', raising=False)
    user = User(auth_subject=str(uuid.uuid4()), auth_provider='https://project.supabase.co/auth/v1')
    with pytest.raises(HTTPException) as failure:
        delete_provider_identity(user)
    assert failure.value.status_code == 503
    monkeypatch.setenv('SUPABASE_SERVICE_ROLE_KEY', 'test-admin-key')
    user.auth_provider = 'https://another.example/auth/v1'
    with pytest.raises(HTTPException) as failure:
        delete_provider_identity(user)
    assert failure.value.status_code == 503


def test_provider_deletion_uses_admin_endpoint_and_accepts_already_deleted(monkeypatch):
    from app.services.account import delete_provider_identity
    from app.models import User
    import httpx
    import uuid
    subject = str(uuid.uuid4())
    monkeypatch.setenv('SUPABASE_URL', 'https://project.supabase.co')
    monkeypatch.setenv('SUPABASE_SERVICE_ROLE_KEY', 'test-admin-key')
    calls = []
    status = 404
    def remove(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(status)
    monkeypatch.setattr(httpx, 'delete', remove)
    user = User(auth_subject=subject, auth_provider='https://project.supabase.co/auth/v1')
    delete_provider_identity(user)
    assert calls[0][0] == f'https://project.supabase.co/auth/v1/admin/users/{subject}'
    assert calls[0][1]['follow_redirects'] is False
    assert calls[0][1]['headers']['Authorization'] == 'Bearer test-admin-key'
    status = 403
    with pytest.raises(HTTPException) as failure:
        delete_provider_identity(user)
    assert failure.value.status_code == 502


def test_raw_deletion_removes_all_versions_only_under_owned_item_prefixes(monkeypatch):
    from app.services.storage import delete_raw_snapshots
    import boto3
    calls = []
    class Storage:
        def get_paginator(self, name):
            assert name == 'list_objects_v2'
            return self
        def paginate(self, **kwargs):
            calls.append(kwargs)
            yield {'Contents': [{'Key': kwargs['Prefix'] + 'v1.json'}, {'Key': kwargs['Prefix'] + 'v2.json'}]}
        def delete_objects(self, **kwargs):
            calls.append(kwargs)
            return {}
    monkeypatch.setenv('S3_BUCKET', 'test-bucket')
    monkeypatch.setenv('S3_ACCESS_KEY', 'test-key')
    monkeypatch.setenv('S3_SECRET_KEY', 'test-secret')
    monkeypatch.setattr(boto3, 'client', lambda *args, **kwargs: Storage())
    delete_raw_snapshots(['item-a'])
    assert calls[0] == {'Bucket': 'test-bucket', 'Prefix': 'raw/item-a/'}
    assert calls[1]['Delete']['Objects'] == [{'Key': 'raw/item-a/v1.json'}, {'Key': 'raw/item-a/v2.json'}]


def test_account_deletion_waits_for_upload_and_no_stale_fetch_can_upload_afterwards(client, sessions, two_users, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.services import account, storage
    from app import database
    from app.models import User
    import boto3
    upload_started, release_upload = Event(), Event()
    deleted = Event()
    calls = []
    class Storage:
        def put_object(self, **kwargs):
            upload_started.set()
            assert release_upload.wait(5)
            calls.append('upload')
    monkeypatch.setenv('S3_BUCKET', 'test-bucket')
    monkeypatch.setenv('S3_ACCESS_KEY', 'test-key')
    monkeypatch.setenv('S3_SECRET_KEY', 'test-secret')
    monkeypatch.setattr(boto3, 'client', lambda *args, **kwargs: Storage())
    monkeypatch.setattr(database, 'SessionLocal', sessions)
    monkeypatch.setattr(account, 'delete_provider_identity', lambda user: None)
    def purge(ids):
        calls.append('purge')
        deleted.set()
    monkeypatch.setattr(account, 'delete_raw_snapshots', purge)
    def remove():
        with sessions() as session:
            account.delete_account(session, session.get(User, two_users['a']))
    with ThreadPoolExecutor(max_workers=2) as pool:
        uploading = pool.submit(storage.store_raw_snapshot, two_users['a_item'], {'text': 'private'})
        assert upload_started.wait(5)
        deleting = pool.submit(remove)
        assert not deleted.is_set()
        release_upload.set()
        assert uploading.result(timeout=5) is not None
        deleting.result(timeout=5)
    assert calls == ['upload', 'purge']
    assert storage.store_raw_snapshot(two_users['a_item'], {'text': 'late private'}) is None
    assert calls == ['upload', 'purge']


def test_fetch_waiting_behind_account_deletion_never_uploads(client, sessions, two_users, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.services import account, storage
    from app import database
    from app.models import User
    import boto3
    initialized = Event()
    calls = []
    class Storage:
        def put_object(self, **kwargs):
            calls.append('unexpected upload')
    def storage_client(*args, **kwargs):
        initialized.set()
        return Storage()
    monkeypatch.setenv('S3_BUCKET', 'test-bucket')
    monkeypatch.setenv('S3_ACCESS_KEY', 'test-key')
    monkeypatch.setenv('S3_SECRET_KEY', 'test-secret')
    monkeypatch.setattr(boto3, 'client', storage_client)
    monkeypatch.setattr(database, 'SessionLocal', sessions)
    monkeypatch.setattr(account, 'delete_provider_identity', lambda user: None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        uploads = []
        def purge(ids):
            uploads.append(pool.submit(storage.store_raw_snapshot, two_users['a_item'], {'text': 'private'}))
            assert initialized.wait(5)
        monkeypatch.setattr(account, 'delete_raw_snapshots', purge)
        with sessions() as session:
            account.delete_account(session, session.get(User, two_users['a']))
        assert uploads[0].result(timeout=5) is None
    assert calls == []
