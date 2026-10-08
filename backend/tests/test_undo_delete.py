"""Undo deletion through the real API, migrations and PostgreSQL."""
import uuid
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic import command
from sqlalchemy import text

from test_phase16_multitenant import (
    SHARED_TOKEN, admin_engine, db, sessions, client, two_users,
)


def scalar(sessions, sql, **params):
    with sessions() as session:
        return session.execute(text(sql), params).scalar()


def expire(sessions, item_id):
    with sessions() as session:
        session.execute(text("UPDATE items SET deleted_at = clock_timestamp() - interval '30 days 1 second' WHERE id = :id"),
                        {'id': item_id})
        session.commit()


def test_soft_delete_hides_and_undo_preserves_the_save(client, sessions, two_users):
    client.as_user(two_users['a'])
    item_id, content_id = two_users['a_item'], two_users['a_asset']
    before = client.request('GET', f'/api/v1/items/{item_id}').json()
    context = client.request('GET', f'/api/v1/memories/{content_id}').json()
    collection = str(uuid.uuid4())
    assert client.request('PUT', f'/api/v1/collections/{collection}',
                          json={'name': 'Keep me', 'urls': [before['canonical_url']]}).status_code == 200
    assert item_id in {r['id'] for r in client.request('GET', '/api/v1/search', params={'q': SHARED_TOKEN}).json()['results']}

    assert client.request('DELETE', f'/api/v1/items/{item_id}', params={'undoable': 'true'}).status_code == 204
    assert scalar(sessions, 'SELECT count(*) FROM items WHERE id=:id', id=item_id) == 1
    assert scalar(sessions, 'SELECT summary FROM items WHERE id=:id', id=item_id) == before['summary']
    assert scalar(sessions, 'SELECT user_note FROM user_memories WHERE user_id=:u AND content_id=:c',
                  u=two_users['a'], c=content_id) == context['note']
    assert client.request('GET', f'/api/v1/items/{item_id}').status_code == 404
    assert item_id not in {r['id'] for r in client.request('GET', '/api/v1/items').json()['items']}
    assert item_id not in {r['id'] for r in client.request('GET', '/api/v1/search', params={'q': SHARED_TOKEN}).json()['results']}
    assert client.request('GET', f'/api/v1/memories/{content_id}').status_code == 404
    assert client.request('PATCH', f'/api/v1/memories/{content_id}', json={'note': 'Changed'}).status_code == 404
    assert client.request('DELETE', f'/api/v1/memories/{content_id}').status_code == 404
    assert client.request('GET', '/api/v1/collections').json()['collections'][0]['urls'] == []
    assert client.request('PUT', f'/api/v1/collections/{uuid.uuid4()}',
                          json={'name': 'Deleted', 'urls': [before['canonical_url']]}).status_code == 404

    assert client.request('POST', f'/api/v1/items/{item_id}/restore').status_code == 204
    assert client.request('GET', f'/api/v1/items/{item_id}').json() == before
    assert client.request('GET', f'/api/v1/memories/{content_id}').json() == context
    assert client.request('GET', '/api/v1/collections').json()['collections'][0]['urls'] == [before['canonical_url']]
    from app.services.deletion import purge_deleted_saves
    with sessions() as session:
        assert purge_deleted_saves(session) == 0


def test_delete_and_restore_retries_do_not_extend_the_window(client, sessions, two_users):
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    stamp = scalar(sessions, 'SELECT deleted_at FROM items WHERE id=:id', id=item_id)
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    assert scalar(sessions, 'SELECT deleted_at FROM items WHERE id=:id', id=item_id) == stamp
    expire(sessions, item_id)
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    assert client.request('POST', f'/api/v1/items/{item_id}/restore').status_code == 409


def test_restore_is_idempotent_within_the_window(client, two_users):
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    assert client.request('POST', f'/api/v1/items/{item_id}/restore').status_code == 204
    assert client.request('POST', f'/api/v1/items/{item_id}/restore').status_code == 204


def test_other_users_cannot_delete_or_restore(client, sessions, two_users):
    client.as_user(two_users['b'])
    item_id = two_users['a_item']
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 404
    assert client.request('POST', f'/api/v1/items/{item_id}/restore').status_code == 404
    assert scalar(sessions, 'SELECT count(*) FROM items WHERE id=:id', id=item_id) == 1
    assert client.request('POST', f'/api/v1/items/{uuid.uuid4()}/restore').status_code == 404
    assert client.request('POST', '/api/v1/items/invalid/restore').status_code == 422


def test_dispatcher_purges_expired_deletes_without_touching_other_users(client, sessions, two_users):
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    from app.services.outbox import dispatch_once
    with sessions() as session:
        dispatch_once(session, publisher=lambda _: None)
    assert scalar(sessions, 'SELECT count(*) FROM items WHERE id=:id', id=item_id) == 1
    expire(sessions, item_id)
    with sessions() as session:
        dispatch_once(session, publisher=lambda _: None)
        dispatch_once(session, publisher=lambda _: None)
    assert scalar(sessions, 'SELECT count(*) FROM items WHERE id=:id', id=item_id) == 0
    assert scalar(sessions, 'SELECT count(*) FROM user_memories WHERE user_id=:u AND content_id=:c',
                  u=two_users['a'], c=two_users['a_asset']) == 0
    assert scalar(sessions, 'SELECT count(*) FROM content_assets WHERE id=:id', id=two_users['a_asset']) == 1
    assert scalar(sessions, 'SELECT count(*) FROM items WHERE id=:id', id=two_users['b_item']) == 1
    assert client.request('POST', f'/api/v1/items/{item_id}/restore').status_code == 404


def test_legacy_delete_remains_permanent(client, sessions, two_users):
    client.as_user(two_users['a'])
    assert client.request('DELETE', f"/api/v1/items/{two_users['a_item']}").status_code == 204
    assert scalar(sessions, 'SELECT count(*) FROM items WHERE id=:id', id=two_users['a_item']) == 0


@pytest.mark.parametrize('batch', [False, True])
def test_saving_again_cancels_a_pending_delete(client, sessions, two_users, batch):
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    if batch:
        response = client.request('POST', '/api/v1/sync/batch', json={'items': [
            {'client_id': str(uuid.uuid4()), 'url': 'https://example.test/alpha'}]})
    else:
        response = client.request('POST', '/api/v1/ingest', json={'url': 'https://example.test/alpha'})
    assert response.status_code == 200
    assert client.request('GET', f'/api/v1/items/{item_id}').status_code == 200
    assert scalar(sessions, 'SELECT deleted_at FROM items WHERE id=:id', id=item_id) is None
    assert scalar(sessions, 'SELECT user_note FROM user_memories WHERE user_id=:u AND content_id=:c',
                  u=two_users['a'], c=two_users['a_asset']) == 'A private note'


def test_expired_resave_starts_fresh_instead_of_reviving_private_context(client, sessions, two_users):
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    expire(sessions, item_id)
    response = client.request('POST', '/api/v1/ingest', json={'url': 'https://example.test/alpha'})
    assert response.status_code == 200
    assert response.json()['id'] != item_id
    assert scalar(sessions, 'SELECT user_note FROM user_memories WHERE user_id=:u AND content_id=:c',
                  u=two_users['a'], c=two_users['a_asset']) is None


def test_migration_rollback_preserves_pending_save_data(db, sessions, client, two_users):
    from app.database import alembic_config
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    config = alembic_config()
    config.set_main_option('sqlalchemy.url', db.url.render_as_string(hide_password=False))
    command.downgrade(config, '0014_collections')
    assert scalar(sessions, 'SELECT count(*) FROM items WHERE id=:id', id=item_id) == 1
    assert scalar(sessions, 'SELECT user_note FROM user_memories WHERE user_id=:u AND content_id=:c',
                  u=two_users['a'], c=two_users['a_asset']) == 'A private note'
    command.upgrade(config, 'head')
    assert scalar(sessions, 'SELECT deleted_at FROM items WHERE id=:id', id=item_id) is None


@pytest.mark.parametrize('elapsed, status', [(2591999.999, 204), (2592000, 409), (2592000.001, 409)])
def test_exact_thirty_day_boundary(client, sessions, two_users, monkeypatch, elapsed, status):
    from app.services import deletion
    now = datetime.datetime.now(datetime.timezone.utc)
    monkeypatch.setattr(deletion, '_now', lambda _: now)
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    with sessions() as session:
        session.execute(text('UPDATE items SET deleted_at=:stamp WHERE id=:id'),
                        {'stamp': now - datetime.timedelta(seconds=elapsed), 'id': item_id})
        session.commit()
    assert client.request('POST', f'/api/v1/items/{item_id}/restore').status_code == status
    with sessions() as session:
        assert deletion.purge_deleted_saves(session) == (0 if status == 204 else 1)


def test_aliases_are_restored_and_purged_without_deleting_a_shared_save(client, sessions, two_users):
    from app.models import Item, UserMemory
    from app.services.deletion import purge_deleted_saves
    with sessions() as session:
        session.execute(text("UPDATE content_assets SET visibility='PUBLIC' WHERE id=:id"), {'id': two_users['a_asset']})
        session.add(UserMemory(user_id=two_users['b'], content_id=two_users['a_asset'], user_note='B keeps this'))
        session.flush()
        alias = Item(user_id=two_users['a'], content_id=two_users['a_asset'], status='ready',
                     url='https://example.test/alias', canonical_url='https://example.test/alias')
        shared = Item(user_id=two_users['b'], content_id=two_users['a_asset'], status='ready',
                      url='https://example.test/alpha', canonical_url='https://example.test/alpha')
        session.add_all([alias, shared])
        session.flush()
        alias_id, shared_id = str(alias.id), str(shared.id)
        session.commit()
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    assert client.request('GET', f'/api/v1/items/{alias_id}').status_code == 404
    client.as_user(two_users['b'])
    assert client.request('GET', f'/api/v1/items/{shared_id}').status_code == 200
    client.as_user(two_users['a'])
    assert client.request('POST', f'/api/v1/items/{alias_id}/restore').status_code == 204
    assert client.request('GET', f'/api/v1/items/{item_id}').status_code == 200
    assert client.request('GET', f'/api/v1/items/{alias_id}').status_code == 200
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    with sessions() as session:
        session.execute(text("UPDATE items SET deleted_at=clock_timestamp() - interval '30 days 1 second' WHERE user_id=:u AND content_id=:c"),
                        {'u': two_users['a'], 'c': two_users['a_asset']})
        session.commit()
        assert purge_deleted_saves(session) == 2
    assert scalar(sessions, 'SELECT count(*) FROM items WHERE id=:id', id=shared_id) == 1
    assert scalar(sessions, 'SELECT user_note FROM user_memories WHERE user_id=:u AND content_id=:c',
                  u=two_users['b'], c=two_users['a_asset']) == 'B keeps this'
    assert scalar(sessions, 'SELECT count(*) FROM content_assets WHERE id=:id', id=two_users['a_asset']) == 1


def test_unlinked_legacy_save_can_be_restored_and_purged(client, sessions, two_users):
    from app.models import Item
    from app.services.deletion import purge_deleted_saves
    with sessions() as session:
        item = Item(user_id=two_users['a'], url='https://example.test/legacy',
                    canonical_url='https://example.test/legacy', status='ready')
        session.add(item)
        session.flush()
        item_id = str(item.id)
        session.commit()
    client.as_user(two_users['a'])
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    assert client.request('POST', f'/api/v1/items/{item_id}/restore').status_code == 204
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    expire(sessions, item_id)
    with sessions() as session:
        assert purge_deleted_saves(session) == 1
    assert scalar(sessions, 'SELECT count(*) FROM items WHERE id=:id', id=item_id) == 0
    assert scalar(sessions, 'SELECT user_note FROM user_memories WHERE user_id=:u AND content_id=:c',
                  u=two_users['a'], c=two_users['a_asset']) == 'A private note'


def test_purge_rechecks_after_a_concurrent_undo(client, sessions, two_users, monkeypatch):
    from app.services import deletion
    now = datetime.datetime.now(datetime.timezone.utc)
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    with sessions() as session:
        session.execute(text('UPDATE items SET deleted_at=:stamp WHERE id=:id'), {'stamp': now, 'id': item_id})
        session.commit()
    selected, proceed = threading.Event(), threading.Event()
    original = deletion._locked_save
    def delayed_lock(session, user_id, selected_id):
        selected.set()
        assert proceed.wait(timeout=5)
        return original(session, user_id, selected_id)
    monkeypatch.setattr(deletion, '_locked_save', delayed_lock)
    monkeypatch.setattr(deletion, '_now', lambda _: now + datetime.timedelta(days=30, seconds=1))
    def purge():
        with sessions() as session:
            return deletion.purge_deleted_saves(session)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(purge)
        try:
            assert selected.wait(timeout=5)
            monkeypatch.setattr(deletion, '_locked_save', original)
            monkeypatch.setattr(deletion, '_now', lambda _: now + datetime.timedelta(days=29))
            assert client.request('POST', f'/api/v1/items/{item_id}/restore').status_code == 204
        finally:
            proceed.set()
        assert future.result(timeout=5) == 0
    assert client.request('GET', f'/api/v1/items/{item_id}').status_code == 200


def test_guest_retention_does_not_bypass_the_undo_window(client, sessions, two_users):
    from app.services.retention import purge_expired_guest_staging
    with sessions() as session:
        session.execute(text('UPDATE users SET auth_subject=:subject WHERE id=:u'),
                        {'subject': f'guest:{uuid.uuid4()}', 'u': two_users['a']})
        session.execute(text("UPDATE items SET created_at=clock_timestamp() - interval '30 days' WHERE id=:id"),
                        {'id': two_users['a_item']})
        session.commit()
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    with sessions() as session:
        assert purge_expired_guest_staging(session) == 0
    assert scalar(sessions, 'SELECT count(*) FROM items WHERE id=:id', id=item_id) == 1
    assert client.request('POST', f'/api/v1/items/{item_id}/restore').status_code == 204


def test_pending_work_is_not_published_until_the_save_is_restored(client, sessions, two_users):
    from app.models import JOB_TYPE_PROCESS
    from app.services.outbox import dispatch_once, record_job
    with sessions() as session:
        session.execute(text("UPDATE items SET status='pending' WHERE id=:id"), {'id': two_users['a_item']})
        record_job(session, two_users['a_asset'], JOB_TYPE_PROCESS)
        session.commit()
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    published = []
    with sessions() as session:
        dispatch_once(session, publisher=published.append)
    assert published == []
    assert client.request('POST', f'/api/v1/items/{item_id}/restore').status_code == 204
    with sessions() as session:
        dispatch_once(session, publisher=published.append)
    assert published == [item_id]
