from datetime import datetime, timezone
from sqlalchemy import text
from test_phase16_multitenant import admin_engine, db, sessions, client, two_users


def test_completed_library_import_is_ready_private_idempotent_and_never_processed(client, sessions, two_users, monkeypatch):
    from app.routers import ingest
    def unexpected(*args, **kwargs):
        raise AssertionError('Completed memories must not enter processing')
    monkeypatch.setattr(ingest, '_enqueue', unexpected)
    monkeypatch.setattr(ingest, '_record_processing_job', unexpected)
    monkeypatch.setattr('app.services.capacity.intake_limit', unexpected)
    client.as_user(two_users['a'])
    rows = [{'client_id': f'local-{i}', 'url': f'https://example.test/import-{i}',
             'captured_at': '2026-09-29T10:00:00Z',
             'saved_memory': {'title': f'Original title {i}', 'summary': 'Original brief',
                              'instant_brief': 'Original brief', 'status': 'ready',
                              'brief_source': 'llm', 'tags': ['original tag'],
                              'key_points': ['Original point'], 'edited': True}}
            for i in range(100)]
    ids = []
    for offset in range(0, 100, 20):
        response = client.request('POST', '/api/v1/sync/batch', json={'items': rows[offset:offset+20]})
        assert response.status_code == 200, response.text
        assert response.json()['errors'] == []
        assert all(row['status'] == 'ready' for row in response.json()['mapped'])
        ids.extend(row['id'] for row in response.json()['mapped'])
    assert len(set(ids)) == 100
    replay = client.request('POST', '/api/v1/sync/batch', json={'items': rows[:20]})
    assert [row['id'] for row in replay.json()['mapped']] == ids[:20]
    card = client.request('GET', '/api/v1/items/' + ids[0]).json()
    assert card['instant_brief'] == 'Original brief'
    assert card['tags'] == ['original tag']
    assert card['edited'] is True
    assert datetime.fromisoformat(card['created_at']) == datetime(2026, 9, 29, 10, tzinfo=timezone.utc)
    with sessions() as session:
        assert session.execute(text("SELECT count(*) FROM items WHERE user_id=:uid AND canonical_url LIKE '%/import-%' AND content_id IS NOT NULL"), {'uid': two_users['a']}).scalar() == 0
    client.as_user(two_users['b'])
    assert client.request('GET', '/api/v1/items/' + ids[0]).status_code == 404


def test_import_recovers_an_old_pending_account_copy_without_overwriting_ready_cards(client, sessions, two_users):
    client.as_user(two_users['a'])
    with sessions() as session:
        session.execute(text("UPDATE items SET status='pending', summary=NULL, brief_v2='{}'::jsonb WHERE id=:id"), {'id': two_users['a_item']})
        session.commit()
    data = {'items': [{'client_id': 'repair', 'url': 'https://example.test/alpha',
                      'saved_memory': {'title': 'Original local title', 'summary': 'Original local brief',
                                       'instant_brief': 'Original local brief', 'brief_source': 'llm'}}]}
    response = client.request('POST', '/api/v1/sync/batch', json=data)
    assert response.status_code == 200
    assert response.json()['mapped'][0]['id'] == two_users['a_item']
    assert response.json()['mapped'][0]['status'] == 'ready'
    card = client.request('GET', '/api/v1/items/' + two_users['a_item']).json()
    assert card['instant_brief'] == 'Original local brief'
    with sessions() as session:
        assert session.execute(text('SELECT content_id FROM items WHERE id=:id'), {'id': two_users['a_item']}).scalar() is None
    data['items'][0]['saved_memory']['summary'] = 'A later client must not clobber this'
    client.request('POST', '/api/v1/sync/batch', json=data)
    assert client.request('GET', '/api/v1/items/' + two_users['a_item']).json()['summary'] == 'Original local brief'


def test_queued_worker_does_not_reprocess_an_imported_snapshot(sessions, two_users, monkeypatch):
    from app import tasks
    with sessions() as session:
        session.execute(text("UPDATE items SET content_id=NULL WHERE id=:id"), {'id': two_users['a_item']})
        session.commit()
    monkeypatch.setattr(tasks, 'SessionLocal', sessions)
    def unexpected(*args, **kwargs):
        raise AssertionError('Imported snapshots must not enter processing')
    monkeypatch.setattr(tasks.public_cache, 'serialized', unexpected)
    result = tasks.process_item.run(two_users['a_item'])
    assert result['status'] == 'ready'
    assert result['skipped'] == 'saved snapshot'


def test_worker_loaded_before_import_rechecks_ownership_after_claim(sessions, two_users, monkeypatch):
    from contextlib import contextmanager
    from app import tasks
    from app.models import ProcessingJob
    with sessions() as session:
        session.execute(text("UPDATE items SET status='pending' WHERE id=:id"), {'id': two_users['a_item']})
        session.add(ProcessingJob(content_id=two_users['a_asset'], status='PENDING', job_type=tasks.JOB_TYPE_PROCESS))
        session.commit()
    @contextmanager
    def import_wins(db, url):
        with sessions() as session:
            session.execute(text("UPDATE items SET status='ready', content_id=NULL WHERE id=:id"), {'id': two_users['a_item']})
            session.commit()
        yield
    monkeypatch.setattr(tasks, 'SessionLocal', sessions)
    monkeypatch.setattr(tasks.public_cache, 'serialized', import_wins)
    result = tasks.process_item.run(two_users['a_item'])
    assert result['status'] == 'ready'
    assert 'skipped' in result
    with sessions() as session:
        assert session.execute(text('SELECT content_id FROM items WHERE id=:id'), {'id': two_users['a_item']}).scalar() is None


def test_snapshot_validation_rejects_cache_identity_and_oversized_payloads():
    import pytest
    from pydantic import ValidationError
    from app.schemas import SavedMemorySnapshot
    with pytest.raises(ValidationError):
        SavedMemorySnapshot.model_validate({'title': 'Own memory', 'user_id': 'another-account'})
    with pytest.raises(ValidationError):
        SavedMemorySnapshot.model_validate({'title': 'Own memory', 'entities': {'large': 'x' * 65536}})


def test_guest_cannot_import_an_arbitrary_completed_snapshot(client, sessions, two_users):
    with sessions() as session:
        session.execute(text("UPDATE users SET auth_subject='guest:import-test' WHERE id=:id"), {'id': two_users['a']})
        session.commit()
    client.as_user(two_users['a'])
    response = client.request('POST', '/api/v1/sync/batch', json={'items': [
        {'client_id': 'guest-copy', 'url': 'https://example.test/private-import',
         'saved_memory': {'title': 'Not a processed guest capture'}}]})
    assert response.status_code == 403
