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
