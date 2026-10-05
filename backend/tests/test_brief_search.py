import asyncio
from types import SimpleNamespace
from app.services import embedder, pipeline, search


def test_embedding_includes_tags_and_search_phrases_but_not_missing_info():
    text = embedder.memory_string('Title', 'Facts', ['Point'], {'tools_products': ['Claude']},
                                  tags=['agent skills'], search_phrases=['tools for testing code'])
    assert 'agent skills' in text and 'tools for testing code' in text and 'Claude' in text
    assert 'Open original' not in text


def test_arabic_search_terms_survive_query_parsing():
    terms = search.query_terms('مهارات كتابة الاختبارات Claude')
    assert terms == ['مهارات', 'كتابة', 'الاختبارات', 'claude']
    assert 'مهارات' in search.or_tsquery(terms)


def test_typo_expands_only_to_existing_user_tags():
    class DB:
        def execute(self, stmt, params):
            assert params['uid'] == 'owner'
            return self
        def fetchall(self): return [('claude code',), ('testing',)]
    assert 'claude' in search.correct_tag_terms(DB(), 'owner', ['calude'])
    assert search.correct_tag_terms(DB(), 'owner', ['unrelated']) == ['unrelated']


def test_chunking_uses_full_evidence_after_raw_text_cap():
    item = SimpleNamespace(normalized_text='short prefix', evidence_bundle={'transcript': [
        {'start': 0, 'end': 4, 'text': 'Opening statement.'},
        {'start': 90, 'end': 95, 'text': 'Named skill at the end.'}]})
    asyncio.run(pipeline.stage_chunk(item))
    assert any('Named skill' in c for c in item.chunk_texts)
    assert {'timestamp': '01:30', 'seconds': 90} in item.chunk_timestamps
