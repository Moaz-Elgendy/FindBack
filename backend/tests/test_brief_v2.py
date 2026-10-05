import asyncio
import pytest
from pydantic import ValidationError
from app.schemas import BriefV2
from app.services import brief_v2


def payload():
    return dict(title='Claude Code skills', content_type='tutorial',
                instant_brief='Use Claude Code to write and test code.',
                key_points=[{'point': 'Claude Code: writes code.', 'source_ref': '00:05'}],
                best_takeaway=None, entities={'tools_products': ['Claude Code'], 'people_orgs': [], 'numbers': []},
                topics=['coding'], tags=['claude code', 'claude', 'coding', 'code', 'ai', 'ai tools',
                'developer tools', 'developers', 'software development', 'programming', 'tutorial',
                'skills', 'agent skills', 'testing', 'code writing'],
                search_phrases=['Find Claude Code skills.', 'Tools for writing code.', 'AI tools for developers.',
                                'Claude Code tutorial.', 'Skills for coding.'],
                likely_intent='You may have saved this to write code.', suggested_action=None,
                confidence='high', evidence_used=['transcript'], missing_info=None)


def evidence():
    return dict(source_platform='youtube', url='https://youtube.com/watch?v=x', source_id='x',
                title='Claude Code skills', caption='', transcript=[{'start': 5, 'end': 15,
                'text': 'Use Claude Code to write and test code.'}], evidence_level='full_transcript')


def test_schema_rejects_unknown_and_invalid_fields():
    assert BriefV2(**payload()).key_points[0].source_ref == '00:05'
    for change in ({'surprise': 1}, {'confidence': 'certain'}, {'title': 'x' * 81}):
        with pytest.raises(ValidationError):
            BriefV2(**dict(payload(), **change))


def test_tag_normalization_and_bounds():
    tags = brief_v2.clean_tags(['CALUDE', 'Claude', 'interesting', 'video content', 'one two three four']
                               + payload()['tags'] + ['tag ' + str(i) for i in range(40)])
    assert tags[0] == 'claude'
    assert len(tags) == 30 and len(set(tags)) == 30
    assert all(len(t.split()) <= 3 and t == t.lower() for t in tags)
    assert 'interesting' not in tags and 'video content' not in tags
    with pytest.raises(ValidationError):
        BriefV2(**dict(payload(), tags=['coding']))


def test_invalid_schema_repairs_once_and_never_sends_fetch_errors():
    class Gateway:
        calls = []
        async def generate_json(self, system, user, **kwargs):
            self.calls.append((system, user))
            assert 'secret error' not in user
            return {'invalid': True} if len(self.calls) == 1 else payload()
    gateway = Gateway()
    result = asyncio.run(brief_v2.extract(dict(evidence(), fetch_errors=['secret error']), gateway))
    assert result.title == 'Claude Code skills' and len(gateway.calls) == 2
    assert 'repair' in gateway.calls[1][1].lower()


def test_full_transcript_requires_grounded_timestamp():
    bad = dict(payload(), key_points=[{'point': 'Claude Code', 'source_ref': '99:59'}])
    with pytest.raises(ValueError):
        brief_v2.validate(bad, evidence())


def test_partial_confidence_and_missing_info():
    ev = dict(evidence(), transcript=[], caption='Claude Code writes code.', evidence_level='partial')
    result = brief_v2.validate(dict(payload(), confidence='medium', evidence_used=['caption'],
                                   key_points=[{'point': 'Claude Code writes code.', 'source_ref': 'caption'}],
                                   missing_info='Open original for the remaining steps.'), ev)
    assert result.confidence == 'medium'
    with pytest.raises(ValueError):
        brief_v2.validate(payload(), ev)


def test_fixture_set_runs_real_brief_stages():
    import json
    import importlib.util
    from pathlib import Path
    script = Path(__file__).resolve().parents[2] / 'scripts/eval_brief.py'
    spec = importlib.util.spec_from_file_location('eval_brief', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixtures = json.loads((Path(__file__).parent / 'fixtures/brief_v2.json').read_text())
    for fixture in fixtures:
        result = asyncio.run(module.run(fixture['evidence'], fixture['output']))
        assert all(result['checks'].values()), fixture['name']


def test_malformed_tag_type_is_repaired():
    class Gateway:
        count = 0
        async def generate_json(self, *args, **kwargs):
            self.count += 1
            return dict(payload(), tags=[17]) if self.count == 1 else payload()
    gateway = Gateway()
    result = asyncio.run(brief_v2.extract(evidence(), gateway))
    assert result.title and gateway.count == 2


def test_long_transcript_is_mapped_and_merged_without_losing_timestamps(monkeypatch):
    monkeypatch.setenv('BRIEF_CHUNK_CHARS', '1000')
    ev = evidence()
    ev['transcript'] = [{'start': 5 + i * 10, 'end': 14 + i * 10,
                         'text': ('Use Claude Code to write and test code. ' * 12)} for i in range(4)]
    class Gateway:
        calls = []
        async def generate_json(self, system, user, **kwargs):
            import json
            data = json.loads(user)
            self.calls.append(data)
            output = payload()
            if 'transcript' in data:
                output['key_points'] = [{'point': 'Use Claude Code to write tests.',
                                        'source_ref': brief_v2.timestamp(data['transcript'][0]['start'])}]
            else:
                output['key_points'] = [p for c in data['extracted_chunks'] for p in c['key_points']]
            return output
    gateway = Gateway()
    result = asyncio.run(brief_v2.extract(ev, gateway))
    assert len(gateway.calls) > 1
    assert {p.source_ref for p in result.key_points} == {'00:05', '00:15', '00:25', '00:35'}


def test_oversized_segment_and_many_chunks_keep_every_request_bounded(monkeypatch):
    monkeypatch.setenv('BRIEF_CHUNK_CHARS', '1000')
    ev = evidence()
    ev['transcript'] = [{'start': i * 10, 'end': i * 10 + 5,
                         'text': 'Claude writes tests. ' * 80} for i in range(16)]
    class Gateway:
        calls = []
        async def generate_json(self, system, user, **kwargs):
            import json
            data = json.loads(user)
            self.calls.append(user)
            output = payload()
            if 'transcript' in data:
                assert len(json.dumps(data['transcript'], ensure_ascii=False)) <= 1000
                refs = [brief_v2.timestamp(s['start']) for s in data['transcript']]
            else:
                assert len(data['extracted_chunks']) <= 2
                refs = data['allowed_timestamps']
                assert set(refs) == {p['source_ref'] for c in data['extracted_chunks'] for p in c['key_points']}
            output['key_points'] = [{'point': 'Claude writes tests.', 'source_ref': ref} for ref in dict.fromkeys(refs)]
            return output
    gateway = Gateway()
    result = asyncio.run(brief_v2.extract(ev, gateway))
    assert max(map(len, gateway.calls)) <= 13000
    assert {p.source_ref for p in result.key_points} == {brief_v2.timestamp(i * 10) for i in range(16)}
