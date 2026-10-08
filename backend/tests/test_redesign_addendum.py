"""Home/Find backend contracts and real database processing boundaries."""
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from test_phase16_multitenant import admin_engine, db, sessions, client, two_users


def test_reprocess_requires_confirmation_and_keeps_previous_brief(client, sessions, two_users, monkeypatch):
    from app.routers import ingest
    monkeypatch.setattr(ingest.process_item, 'delay', lambda _: None)
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    old = client.request('PATCH', f'/api/v1/items/{item_id}', json={'summary': 'My own brief'}).json()
    assert old['edited'] is True
    assert client.request('POST', f'/api/v1/items/{item_id}/summarize-again', json={}).status_code == 409
    response = client.request('POST', f'/api/v1/items/{item_id}/summarize-again', json={'replace_edits': True})
    assert response.status_code == 202
    assert response.json()['reprocessing'] is True
    assert response.json()['status'] == 'ready'
    from test_phase16_multitenant import SHARED_TOKEN
    hits = client.request('GET', '/api/v1/search', params={'q': SHARED_TOKEN}).json()['results']
    assert next(hit for hit in hits if hit['id'] == item_id)['reprocessing'] is True
    assert response.json()['summary'] == 'My own brief'
    client.as_user(two_users['b'])
    assert client.request('POST', f'/api/v1/items/{item_id}/summarize-again', json={}).status_code == 404


def test_restore_and_purge_at_exact_thirty_day_boundary(client, sessions, two_users, monkeypatch):
    from app.services import deletion
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    assert client.request('DELETE', f'/api/v1/items/{item_id}?undoable=true').status_code == 204
    with sessions() as session:
        stamp = session.execute(text('SELECT deleted_at FROM items WHERE id=:id'), {'id': item_id}).scalar_one()
    monkeypatch.setattr(deletion, '_now', lambda _: stamp + timedelta(days=30) - timedelta(microseconds=1))
    with sessions() as session:
        assert deletion.purge_deleted_saves(session) == 0
    monkeypatch.setattr(deletion, '_now', lambda _: stamp + timedelta(days=30))
    assert client.request('POST', f'/api/v1/items/{item_id}/restore').status_code == 409
    with sessions() as session:
        assert deletion.purge_deleted_saves(session) == 1


def test_worker_failure_keeps_old_summary_edits_vectors_and_chunks(client, sessions, two_users, monkeypatch):
    from app import tasks
    from app.services import pipeline
    from app.routers import ingest
    monkeypatch.setattr(ingest.process_item, 'delay', lambda _: None)
    monkeypatch.setattr(tasks, 'SessionLocal', sessions)
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    old = client.request('PATCH', f'/api/v1/items/{item_id}', json={'summary': 'My own brief'}).json()
    with sessions() as session:
        session.execute(text("UPDATE items SET embedding=array_fill(0.25::real, ARRAY[1536])::vector WHERE id=:id"), {'id': item_id})
        session.execute(text("INSERT INTO chunks(item_id,chunk_idx,chunk_text,embedding) VALUES(:id,0,'previous chunk',array_fill(0.25::real, ARRAY[1536])::vector)"), {'id': item_id})
        session.commit()
    assert client.request('POST', f'/api/v1/items/{item_id}/summarize-again', json={'replace_edits': True}).status_code == 202
    async def change_then_fail(item, session):
        item.summary = 'replacement must rollback'
        session.execute(text('DELETE FROM chunks WHERE item_id=:id'), {'id': item_id})
        item.embedding = [0.5] * 1536
        raise RuntimeError('SECRET provider token')
    async def noop(item):
        item.summary = 'partially generated'
    monkeypatch.setattr(pipeline, '_STAGES', {name: change_then_fail if name == 'EMBED' else noop for name in pipeline.STAGE_ORDER})
    result = tasks.process_item.run(item_id)
    assert result['status'] == 'ready'
    response = client.request('GET', f'/api/v1/items/{item_id}').json()
    assert response['summary'] == old['summary']
    assert response['edited'] is True
    assert response['reprocessing'] is False
    assert response['reprocess_failure'] and 'SECRET' not in response['reprocess_failure']
    with sessions() as session:
        assert session.execute(text('SELECT chunk_text FROM chunks WHERE item_id=:id'), {'id': item_id}).scalar_one() == 'previous chunk'
        assert session.execute(text('SELECT embedding::text FROM items WHERE id=:id'), {'id': item_id}).scalar_one().startswith('[0.25,')


def test_automatic_processing_skips_edited_item(client, sessions, two_users, monkeypatch):
    from app import tasks
    from app.services.outbox import _pending_item_ids, recover_briefs
    monkeypatch.setattr(tasks, 'SessionLocal', sessions)
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    client.request('PATCH', f'/api/v1/items/{item_id}', json={'title': 'My title'})
    with sessions() as session:
        session.execute(text("UPDATE items SET status='pending', needs_retry=true WHERE id=:id"), {'id': item_id})
        session.commit()
        assert _pending_item_ids(session, two_users['a_asset']) == []
    assert tasks.process_item.run(item_id)['skipped'] == 'edited'


def test_description_only_uses_explicit_evidence_and_matched_terms_are_real(client, sessions, two_users):
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    with sessions() as session:
        session.execute(text("UPDATE items SET evidence_bundle='{}', title_clean='quokka', summary='wallaby' WHERE id=:id"), {'id': item_id})
        session.commit()
    response = client.request('GET', f'/api/v1/items/{item_id}').json()
    assert response['description_only'] is False
    with sessions() as session:
        session.execute(text('UPDATE items SET evidence_bundle=:e WHERE id=:id'), {'id': item_id, 'e': '{"evidence_level":"metadata_only"}'})
        session.commit()
    assert client.request('GET', f'/api/v1/items/{item_id}').json()['description_only'] is True
    results = client.request('GET', '/api/v1/search', params={'q': 'quokka'}).json()['results']
    hit = next(r for r in results if r['id'] == item_id)
    assert hit['matched_terms'] == ['quokka']
    assert hit['description_only'] is True


def test_successful_reprocess_replaces_edits_only_when_finished(client, sessions, two_users, monkeypatch):
    from app import tasks
    from app.services import pipeline
    from app.routers import ingest
    monkeypatch.setattr(ingest.process_item, 'delay', lambda _: None)
    monkeypatch.setattr(tasks, 'SessionLocal', sessions)
    client.as_user(two_users['a'])
    item_id = two_users['a_item']
    client.request('PATCH', f'/api/v1/items/{item_id}', json={'title': 'My title', 'summary': 'My brief'})
    assert client.request('POST', f'/api/v1/items/{item_id}/summarize-again', json={'replace_edits': True}).status_code == 202
    async def stage(item):
        item.title_clean = 'New generated title'
        item.summary = 'New generated brief'
        item.needs_retry = False
    async def embed(item, session):
        await stage(item)
    monkeypatch.setattr(pipeline, '_STAGES', {name: embed if name == 'EMBED' else stage for name in pipeline.STAGE_ORDER})
    assert tasks.process_item.run(item_id)['status'] == 'ready'
    response = client.request('GET', f'/api/v1/items/{item_id}').json()
    assert response['summary'] == 'New generated brief'
    assert response['title_clean'] == 'New generated title'
    assert response['edited'] is False
    assert response['reprocessing'] is False
    assert response['reprocess_failure'] is None


def test_automatic_retry_schedule_does_not_touch_edited_save():
    from types import SimpleNamespace
    from app.services import brief_retry
    item = SimpleNamespace(edited_title='Personal title', edited_summary=None, needs_retry=True)
    assert brief_retry.should_retry(item) is False
    assert brief_retry.schedule(None, item, None) is False
    assert item.needs_retry is True


def test_semantic_only_candidate_has_no_lexical_terms(monkeypatch):
    import time
    from app.services import search
    row = (uuid.uuid4(), 'Neural slides', 'Deck assistant', [], 'article', 'example.test', None, datetime.now(timezone.utc), None, 0.9)
    monkeypatch.setattr(search, 'correct_tag_terms', lambda *args: ['presentation'])
    for name in ('lexical_search', 'chunk_lexical_search', 'note_search', '_chunk_vector_rows'):
        monkeypatch.setattr(search, name, lambda *args, **kwargs: [])
    monkeypatch.setattr(search, 'vector_search', lambda *args, **kwargs: [row])
    monkeypatch.setattr(search, '_documents', lambda *args: {})
    monkeypatch.setattr(search, '_with_notes', lambda *args: {})
    results, _ = search._rank_candidates(None, uuid.uuid4(), 'presentation', None, None, 10, {}, time.time())
    assert len(results) == 1
    assert results[0]['matched_terms'] == []
    assert results[0]['match_reason'] == 'similar meaning'
