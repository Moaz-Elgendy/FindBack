import json
import pytest
from fastapi import HTTPException
from app.models import Item
from test_phase8_stages import admin_engine, db, sessions, _seed


def test_combined_filters_select_saved_intelligence_before_pagination(db, sessions):
    from app.services.intelligence import query_for, read_filters
    item_id, _, _ = _seed(sessions)
    with sessions() as session:
        item = session.get(Item, item_id)
        item.brief_v2 = {'topics': ['AI'], 'content_type': 'ai_tool',
                         'likely_intent': 'Try later', 'suggested_action': 'Test tool'}
        item.entities = {'tools_products': ['Claude', 'Gamma']}
        item.source_domain = 'youtube.com'
        session.add(Item(user_id=item.user_id, url='https://example.com/other',
                         canonical_url='https://example.com/other', brief_v2={'topics': ['Food']}))
        session.commit()
        criteria = read_filters(json.dumps({'topic': 'AI', 'type': 'ai_tool', 'entity': 'Claude',
                                             'intent': 'Try later', 'action': 'Test tool',
                                             'source': 'youtube.com', 'saved': item.created_at.date().isoformat()}))
        assert [str(row.id) for row in query_for(session, item.user_id, criteria).limit(1)] == [str(item_id)]
        assert query_for(session, item.user_id, {'topic': 'Food'}).count() == 1
        assert query_for(session, item.user_id, {'entity': 'Clau'}).count() == 0


@pytest.mark.parametrize('value', ['{"unknown":"AI"}', '{"saved":"yesterday"}', '{"topic":3}', '[]'])
def test_invalid_intelligence_filters_are_rejected(value):
    from app.services.intelligence import read_filters
    with pytest.raises(HTTPException) as exc:
        read_filters(value)
    assert exc.value.status_code == 422


def test_search_intelligence_filter_applies_before_candidate_limit(db, sessions, monkeypatch):
    import asyncio
    from app.services import search
    from app.services.search import lexical_search
    async def no_embedding(*args, **kwargs):
        return None
    monkeypatch.setattr(search.embedder, "embed_text", no_embedding)
    from app.services.intelligence import query_for
    item_id, _, _ = _seed(sessions)
    with sessions() as session:
        item = session.get(Item, item_id)
        item.status = 'ready'
        item.title_clean = 'Claude tools'
        item.search_text = 'Claude tools'
        item.brief_v2 = {'topics': ['AI'], 'brief_source': 'llm'}
        for index in range(60):
            session.add(Item(user_id=item.user_id, url=f'https://example.com/{index}',
                canonical_url=f'https://example.com/{index}', status='ready',
                title_clean='Claude tools', search_text='Claude tools', brief_v2={'topics': ['Food']}))
        session.commit()
        ids = {str(row.id) for row in query_for(session, item.user_id, {'topic': 'AI'})}
        result = lexical_search(session, item.user_id, 'claude', ['claude'], limit=1, intelligence_ids=ids)
        assert [str(row[0]) for row in result] == [str(item_id)]
        ranked, _ = asyncio.run(search.hybrid_search(session, item.user_id, 'claude', limit=1, intelligence={'topic': 'AI'}))
        assert [str(hit['row'][0]) for hit in ranked] == [str(item_id)]
        import httpx
        from types import SimpleNamespace
        from app.main import app
        from app.database import get_db
        from app.auth import get_current_user
        previous = dict(app.dependency_overrides)
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=item.user_id)
        try:
            async def api_requests():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                    parameters = {'intelligence': json.dumps({'topic': 'AI'}), 'limit': 1}
                    response = await client.get('/api/v1/items', params=parameters)
                    assert response.status_code == 200
                    assert [row['id'] for row in response.json()['items']] == [str(item_id)]
                    response = await client.get('/api/v1/search', params={**parameters, 'q': 'claude'})
                    assert response.status_code == 200
                    assert response.json()['results'][0]['topics'] == ['AI']
                    assert response.json()['results'][0]['id'] == str(item_id)
                    assert (await client.get('/api/v1/items', params={'intelligence': '{"unknown":"x"}'})).status_code == 422
            asyncio.run(api_requests())
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
