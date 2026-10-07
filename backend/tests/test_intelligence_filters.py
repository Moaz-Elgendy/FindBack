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
        item.entities = {'tools_products': ['Claude', 'Gamma'], 'people_orgs': ['Ada Lovelace'], 'numbers': ['42'], 'unknown': ['noise']}
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
        assert query_for(session, item.user_id, {'entity': 'Ada Lovelace'}).count() == 1
        assert query_for(session, item.user_id, {'entity': '42'}).count() == 0
        assert query_for(session, item.user_id, {'entity': 'noise'}).count() == 0


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


def test_entity_groups_and_hostile_filters(db, sessions):
    from app.models import Item
    from app.services.intelligence import query_for
    item_id, _, _ = _seed(sessions)
    with sessions() as session:
        item = session.get(Item, item_id)
        item.entities = {'tools_products': ['Claude Code Setup']}
        item.brief_v2 = {'topics': ['AI']}
        session.commit()
        assert query_for(session, item.user_id, {'entity': 'Claude'}).count() == 1
        assert query_for(session, item.user_id, {'topic': 'AI'}).count() == 1
        assert query_for(session, item.user_id, {'entity': 'Clau'}).count() == 0
        for key in ('entity', 'topic', 'source'):
            assert query_for(session, item.user_id, {key: "' OR 1=1; DROP TABLE items; --"}).count() == 0
        assert session.get(Item, item_id) is not None


def test_irrelevant_vectors_and_hostile_search_do_not_return_everything(db, sessions, monkeypatch):
    from app.models import Item
    from app.services import search
    item_id, _, _ = _seed(sessions)
    with sessions() as session:
        item = session.get(Item, item_id)
        item.status = 'ready'
        item.brief_v2 = {'brief_source': 'llm'}
        item.embedding = [1.0] + [0.0] * 1535
        session.commit()
        irrelevant = [0.5, 3 ** 0.5 / 2] + [0.0] * 1534
        import asyncio
        async def query_embedding(*args, **kwargs):
            return irrelevant
        monkeypatch.setattr(search.embedder, 'embed_text', query_embedding)
        results, _ = asyncio.run(search.hybrid_search(session, item.user_id, 'banana bread'))
        assert results == []
        assert len(search.vector_search(session, item.user_id, [1.0] + [0.0] * 1535)) == 1
        query = "' OR 1=1; DROP TABLE items; --"
        assert search.lexical_search(session, item.user_id, query, search.query_terms(query)) == []
        assert search.vector_search(session, item.user_id, [1.0] + [0.0] * 1535, category=query) == []
        assert session.get(Item, item_id) is not None


def test_broad_topic_groups_cover_subjects_not_people_and_remain_user_scoped(db, sessions):
    from app.models import User
    from app.services.intelligence import query_for
    item_id, _, _ = _seed(sessions)
    with sessions() as session:
        original = session.get(Item, item_id)
        original.brief_v2 = {'topics': ['AI', 'Programming']}
        original.title = original.title_clean = 'Claude Code skills'
        session.flush()
        other = User(email='topic-other@example.test')
        session.add(other)
        session.flush()
        rows = [
            Item(user_id=original.user_id, url='https://example.test/extensions', title_clean='Five Claude Code extensions', brief_v2={'topics': ['AI', 'Programming']}),
            Item(user_id=original.user_id, url='https://example.test/plugins', brief_v2={'topics': ['AI', 'Design']}),
            Item(user_id=original.user_id, url='https://example.test/food', title_clean='Claude explains cooking', brief_v2={'topics': ['Food']}),
            Item(user_id=original.user_id, url='https://example.test/gym', brief_v2={'topics': ['Gym']}),
            Item(user_id=original.user_id, url='https://example.test/device', brief_v2={'topics': ['Electronics']}),
            Item(user_id=original.user_id, url='https://example.test/author', entities={'people_orgs': ['Claude']}, brief_v2={'topics': ['History']}),
            Item(user_id=original.user_id, url='https://example.test/arabic', brief_v2={'topics': ['AI']}),
            Item(user_id=other.id, url='https://example.test/private', brief_v2={'topics': ['AI']}),
        ]
        for row in rows:
            row.canonical_url = row.url
        session.add_all(rows)
        session.commit()
        expected = {original.id, rows[0].id, rows[1].id, rows[6].id}
        assert {row.id for row in query_for(session, original.user_id, {'topic': 'AI'})} == expected
        for label, index in [('Food', 2), ('Gym', 3), ('Electronics', 4), ('History', 5)]:
            assert {row.id for row in query_for(session, original.user_id, {'topic': label})} == {rows[index].id}
        assert query_for(session, original.user_id, {'topic': "' OR 1=1; DROP TABLE items; --"}).count() == 0
        assert query_for(session, original.user_id, {'topic': 'Programming'}).count() == 2
        original.title_clean = ''
        original.title = 'Claude demo'
        original.brief_v2 = {'topics': []}
        session.commit()
        assert original.id not in {row.id for row in query_for(session, original.user_id, {'topic': 'AI'})}
        original.title_clean = original.title = 'History'
        original.summary = None
        original.brief_v2 = {'topics': [], 'instant_brief': 'Claude coding demo'}
        session.commit()
        assert original.id not in {row.id for row in query_for(session, original.user_id, {'topic': 'AI'})}



def test_semantic_topic_catalogue_matches_the_offline_client():
    import ast
    from pathlib import Path
    from app.services.intelligence import TOPIC_LABELS
    source = (Path(__file__).resolve().parents[2] / 'mobile/lib/models/search_result.dart').read_text()
    literal = source.split('const topicLabels = <String> ', 1)[1].split(';', 1)[0]
    assert tuple(ast.literal_eval(literal)) == TOPIC_LABELS


def test_semantic_topics_reject_junk_and_other_without_keyword_inference():
    from app.services.intelligence import semantic_topics
    assert semantic_topics(['science', 'History']) == ['Science', 'History']
    assert semantic_topics(['AI', 'AI']) == ['AI']
    for value in (['Other'], ['Claude Code skills'], ['AI', 'Programming', 'Design'], ['Unknown'], 'AI'):
        with pytest.raises(ValueError):
            semantic_topics(value)


def test_legacy_semantic_topics_are_projected_and_filterable(db, sessions):
    from app.schemas import ItemDetail
    from app.services.intelligence import query_for

    item_id, _, _ = _seed(sessions)
    with sessions() as session:
        item = session.get(Item, item_id)
        item.brief_v2 = None
        item.fetch_metadata = {'brief': {'topics': ['History']}}
        session.commit()
        assert ItemDetail.model_validate(item).topics == ['History']
        assert query_for(session, item.user_id, {'topic': 'History'}).count() == 1
        assert query_for(session, item.user_id, {'topic': 'AI'}).count() == 0
