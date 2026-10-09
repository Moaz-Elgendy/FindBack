"""Redesign item actions against the real API and PostgreSQL."""
import uuid

import pytest
from sqlalchemy import text

from test_phase16_multitenant import admin_engine, db, sessions, client, two_users


def update(sessions, item_id, assignments):
    with sessions() as session:
        session.execute(text(f'UPDATE items SET {assignments} WHERE id=:id'), {'id': item_id})
        session.commit()


def test_edit_is_private_persistent_and_searchable(client, sessions, two_users):
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    response = client.request('PATCH', f'/api/v1/items/{item_id}',
                              json={'title': 'My quokka memory', 'summary': 'Personal wallaby brief'})
    assert response.status_code == 200
    for item in [response.json(), client.request('GET', f'/api/v1/items/{item_id}').json(),
                 client.request('GET', '/api/v1/items').json()['items'][0]]:
        assert item['title_clean'] == 'My quokka memory'
        assert item['summary'] == 'Personal wallaby brief'
        assert item['instant_brief'] == 'Personal wallaby brief'
    hits = client.request('GET', '/api/v1/search', params={'q': 'quokka'}).json()['results']
    assert any(hit['id'] == item_id and hit['summary'] == 'Personal wallaby brief' for hit in hits)
    with sessions() as session:
        assert session.execute(text('SELECT title FROM content_assets WHERE id=:id'),
                               {'id': two_users['a_asset']}).scalar() != 'My quokka memory'
    client.as_user(two_users['b'])
    assert not client.request('GET', '/api/v1/search', params={'q': 'quokka'}).json()['results']


@pytest.mark.parametrize('payload', [{}, {'url': 'https://example.test'}, {'title': ''},
                                    {'title': '  '}, {'title': None}, {'summary': None},
                                    {'title': 'x' * 501}, {'summary': 'x' * 20001}])
def test_edit_validates_payload(client, two_users, payload):
    client.as_user(two_users['a'])
    assert client.request('PATCH', f"/api/v1/items/{two_users['a_item']}", json=payload).status_code == 422


def test_partial_edit_can_clear_brief_and_survives_processing(client, sessions, two_users):
    from app.models import Item
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    assert client.request('PATCH', f'/api/v1/items/{item_id}', json={'summary': ''}).status_code == 200
    with sessions() as session:
        item = session.get(Item, uuid.UUID(item_id))
        item.summary = 'Later generated content'
        item.title_clean = 'Later generated title'
        session.commit()
    item = client.request('GET', f'/api/v1/items/{item_id}').json()
    assert item['summary'] == item['instant_brief'] == ''
    assert item['title_clean'] == 'Later generated title'


@pytest.mark.parametrize('method, suffix, payload', [('PATCH', '', {'title': 'Secret'}),
                                                    ('POST', '/retry', None),
                                                    ('POST', '/keep-link', None)])
def test_actions_hide_other_users_and_deleted_items(client, two_users, method, suffix, payload):
    item_id = two_users['a_item']
    client.as_user(two_users['b'])
    assert client.request(method, f'/api/v1/items/{item_id}{suffix}', json=payload).status_code == 404
    client.as_user(two_users['a'])
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    assert client.request(method, f'/api/v1/items/{item_id}{suffix}', json=payload).status_code == 404


@pytest.mark.parametrize('reason, expected', [('403 https://secret.test?token=private', 'This page requires access or sign-in.'),
                                            ('timeout API_KEY=secret', 'This page was temporarily unavailable. Try again.'),
                                            ('raw provider stack trace', 'This page could not be read. Try again or keep the link.')])
def test_failure_reason_is_safe_and_available_in_list(client, sessions, two_users, reason, expected):
    with sessions() as session:
        session.execute(text("UPDATE items SET status='failed', failure_reason=:reason WHERE id=:id"),
                        {'id': two_users['a_item'], 'reason': reason})
        session.commit()
    client.as_user(two_users['a'])
    for item in [client.request('GET', f"/api/v1/items/{two_users['a_item']}").json(),
                 client.request('GET', '/api/v1/items').json()['items'][0]]:
        assert item['failure_reason'] == expected


def test_retry_is_durable_idempotent_and_does_not_count_as_a_save(client, sessions, two_users, monkeypatch):
    from app.routers import ingest
    calls = []
    def unavailable(item_id):
        calls.append(item_id)
        raise ConnectionError('queue offline')
    monkeypatch.setattr(ingest.process_item, 'delay', unavailable)
    item_id = two_users['a_item']
    update(sessions, item_id, "status='failed', failure_reason='timeout'")
    client.as_user(two_users['a'])
    response = client.request('POST', f'/api/v1/items/{item_id}/retry')
    assert response.status_code == 202
    assert response.json()['status'] == 'pending'
    assert response.json()['failure_reason'] is None
    assert client.request('POST', f'/api/v1/items/{item_id}/retry').status_code == 202
    assert calls == [item_id]
    with sessions() as session:
        assert session.execute(text("SELECT count(*) FROM processing_jobs WHERE content_id=:id AND status='PENDING'"),
                               {'id': two_users['a_asset']}).scalar() == 1
        assert session.execute(text('SELECT save_count FROM user_memories WHERE content_id=:id AND user_id=:uid'),
                               {'id': two_users['a_asset'], 'uid': two_users['a']}).scalar() == 1


def test_keep_link_stops_queued_processing_and_preserves_url(client, sessions, two_users, monkeypatch):
    from app import tasks
    from app.services.outbox import _pending_item_ids
    item_id = two_users['a_item']
    update(sessions, item_id, "status='failed', failure_reason='timeout', needs_retry=true")
    client.as_user(two_users['a'])
    original = client.request('GET', f'/api/v1/items/{item_id}').json()['url']
    for _ in range(2):
        response = client.request('POST', f'/api/v1/items/{item_id}/keep-link')
        assert response.status_code == 200
        assert response.json()['link_only'] is True
        assert response.json()['status'] == 'ready'
        assert response.json()['url'] == original
        assert response.json()['failure_reason'] is None
        assert response.json()['needs_retry'] is False
    with sessions() as session:
        assert not _pending_item_ids(session, two_users['a_asset'])
    monkeypatch.setattr(tasks, 'SessionLocal', sessions)
    assert tasks.process_item.run(item_id)['skipped'] == 'link only'
    assert client.request('POST', f'/api/v1/items/{item_id}/retry').status_code == 409


def test_ready_items_reject_failure_actions(client, two_users):
    client.as_user(two_users['a'])
    for action in ['retry', 'keep-link']:
        assert client.request('POST', f"/api/v1/items/{two_users['a_item']}/{action}").status_code == 409


def test_active_worker_rejects_fallback_actions(client, sessions, two_users):
    from app.models import ProcessingJob, JOB_TYPE_PROCESS
    item_id = two_users['a_item']
    update(sessions, item_id, "status='ready', needs_retry=true")
    with sessions() as session:
        session.add(ProcessingJob(content_id=uuid.UUID(two_users['a_asset']), job_type=JOB_TYPE_PROCESS,
                                  status='PROCESSING', attempt_token=uuid.uuid4()))
        session.commit()
    client.as_user(two_users['a'])
    for action in ['retry', 'keep-link']:
        assert client.request('POST', f'/api/v1/items/{item_id}/{action}').status_code == 409
    with sessions() as session:
        assert not session.execute(text('SELECT link_only FROM items WHERE id=:id'), {'id': item_id}).scalar()


def test_retry_publishes_and_supports_legacy_unlinked_items(client, sessions, two_users, monkeypatch):
    from app.routers import ingest
    calls = []
    monkeypatch.setattr(ingest.process_item, 'delay', lambda item_id: calls.append(item_id))
    item_id = two_users['a_item']
    update(sessions, item_id, "status='failed', content_id=NULL")
    client.as_user(two_users['a'])
    assert client.request('POST', f'/api/v1/items/{item_id}/retry').status_code == 202
    assert calls == [item_id]
    with sessions() as session:
        content_id = session.execute(text('SELECT content_id FROM items WHERE id=:id'), {'id': item_id}).scalar()
        assert content_id is not None
        assert session.execute(text("SELECT status FROM processing_jobs WHERE content_id=:id"),
                               {'id': content_id}).scalar() == 'PROCESSING'


def test_keep_link_does_not_cancel_another_users_shared_job(client, sessions, two_users, monkeypatch):
    from app import tasks
    from app.models import Item, UserMemory, ProcessingJob, JOB_TYPE_PROCESS
    from app.services.outbox import _pending_item_ids
    item_id, content_id = two_users['a_item'], uuid.UUID(two_users['a_asset'])
    update(sessions, item_id, "status='failed'")
    with sessions() as session:
        session.add(UserMemory(user_id=two_users['b'], content_id=content_id))
        session.flush()
        other = Item(user_id=two_users['b'], content_id=content_id, url='https://example.test/shared',
                     canonical_url='https://example.test/shared', status='pending')
        session.add(other)
        session.add(ProcessingJob(content_id=content_id, job_type=JOB_TYPE_PROCESS))
        session.commit()
        other_id = other.id
    client.as_user(two_users['a'])
    original_claim = tasks.claim_job
    def keep_before_claim(session, job_id):
        assert client.request('POST', f'/api/v1/items/{item_id}/keep-link').status_code == 200
        return original_claim(session, job_id)
    monkeypatch.setattr(tasks, 'SessionLocal', sessions)
    monkeypatch.setattr(tasks, 'claim_job', keep_before_claim)
    assert tasks.process_item.run(item_id)['skipped'] == 'link only'
    with sessions() as session:
        assert _pending_item_ids(session, content_id) == [other_id]
        assert session.execute(text('SELECT status FROM processing_jobs WHERE content_id=:id'),
                               {'id': content_id}).scalar() == 'PENDING'


def test_link_only_fallback_remains_searchable(client, sessions, two_users):
    update(sessions, two_users['a_item'], "status='ready', title_clean='Quokka link', needs_retry=true, brief_v2='{\"brief_source\":\"fallback\"}'")
    client.as_user(two_users['a'])
    assert client.request('POST', f"/api/v1/items/{two_users['a_item']}/keep-link").status_code == 200
    response = client.request('GET', '/api/v1/search', params={'q': 'quokka'}).json()
    assert any(item['id'] == two_users['a_item'] for item in response['results'])


def test_item_actions_migration_preserves_edits_on_rollback(db, sessions, two_users):
    from alembic import command
    from app.database import alembic_config
    cfg = alembic_config()
    cfg.set_main_option('sqlalchemy.url', db.url.render_as_string(hide_password=False))
    update(sessions, two_users['a_item'], "edited_title='User title', edited_summary='User brief'")
    command.downgrade(cfg, '0015_undo_delete')
    with db.connect() as conn:
        row = conn.execute(text('SELECT title_clean, summary FROM items WHERE id=:id'),
                           {'id': two_users['a_item']}).one()
        assert tuple(row) == ('User title', 'User brief')
        assert conn.execute(text('SELECT count(*) FROM items')).scalar() == 2
    command.upgrade(cfg, 'head')
    with db.connect() as conn:
        assert conn.execute(text('SELECT link_only FROM items WHERE id=:id'),
                            {'id': two_users['a_item']}).scalar() is False


# --- opening a save (first_opened_at) ---------------------------------------

def test_open_records_the_first_open_of_an_own_save(client, sessions, two_users):
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    assert client.request('POST', f'/api/v1/items/{item_id}/open').status_code == 204
    with sessions() as session:
        opened = session.execute(text('SELECT first_opened_at FROM items WHERE id=:id'),
                                 {'id': item_id}).scalar()
    assert opened is not None


def test_open_of_another_users_save_is_not_found_and_stores_nothing(client, sessions, two_users):
    client.as_user(two_users['b'])
    assert client.request('POST', f"/api/v1/items/{two_users['a_item']}/open").status_code == 404
    with sessions() as session:
        assert session.execute(text('SELECT first_opened_at FROM items WHERE id=:id'),
                               {'id': two_users['a_item']}).scalar() is None


def test_open_of_a_deleted_save_is_not_found(client, sessions, two_users):
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    assert client.request('POST', f'/api/v1/items/{item_id}/open').status_code == 404
    with sessions() as session:
        assert session.execute(text('SELECT first_opened_at FROM items WHERE id=:id'),
                               {'id': item_id}).scalar() is None


def test_open_is_idempotent_and_keeps_the_first_timestamp(client, sessions, two_users):
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    assert client.request('POST', f'/api/v1/items/{item_id}/open').status_code == 204
    with sessions() as session:
        first = session.execute(text('SELECT first_opened_at FROM items WHERE id=:id'),
                                {'id': item_id}).scalar()
    assert first is not None
    # Repeat calls answer the same way and must not move the stored instant.
    for _ in range(2):
        assert client.request('POST', f'/api/v1/items/{item_id}/open').status_code == 204
    with sessions() as session:
        again = session.execute(text('SELECT first_opened_at FROM items WHERE id=:id'),
                                {'id': item_id}).scalar()
    assert again == first
