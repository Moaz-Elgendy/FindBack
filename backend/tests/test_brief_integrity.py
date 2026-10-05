import asyncio
from types import SimpleNamespace
import pytest
from app.services import brief_v2, brief_retry, pipeline
from test_brief_v2 import evidence, payload


def test_fallback_is_not_transcript_dump_and_always_needs_retry():
    ev = evidence()
    data, legacy = brief_v2.offline(ev)
    assert data['confidence'] == 'low'
    assert data['tags'] == [] and data['key_points'] == []
    assert data['missing_info'] and data['brief_source'] == 'fallback'
    item = SimpleNamespace(url=ev['url'], evidence_bundle=ev, brief_v2=data,
                           processing_metadata={'media_attempts': 99})
    assert brief_retry.should_retry(item)
    assert not legacy.highlights


def test_raw_transcript_and_unsupported_brand_are_rejected():
    ev = evidence()
    with pytest.raises(ValueError):
        brief_v2.validate(dict(payload(), key_points=[{'point':ev['transcript'][0]['text'], 'source_ref':'00:05'}]),ev)
    with pytest.raises(ValueError):
        brief_v2.validate(dict(payload(),tags=payload()['tags']+['anthropic']),ev)


def test_native_provenance_and_fallback_flag(monkeypatch):
    async def fail(*args): raise TimeoutError('provider outage')
    monkeypatch.setattr(brief_v2,'extract',fail)
    item=SimpleNamespace(id='test',url=evidence()['url'],title=evidence()['title'],
                         fetch_metadata={},evidence_bundle=evidence(),processing_metadata={})
    asyncio.run(pipeline.stage_understand(item))
    assert item.brief_v2['brief_source']=='fallback'
    assert item.brief_v2['prompt_version']==brief_v2.PROMPT_VERSION
    assert item.brief_v2['evidence_level']=='full_transcript'
    assert item.needs_retry


def test_eval_rejects_fallback_noise_title_and_copied_points():
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[2]/'scripts/eval_brief.py'
    spec=importlib.util.spec_from_file_location('eval_integrity',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    ev=evidence()
    out=dict(payload(),brief_source='fallback',title='194 reactions · 7 comments | Claude',
             tags=['most','only','help automatically'],key_points=[{'point':ev['transcript'][0]['text'],'source_ref':'00:05'}])
    checks=module.checks(out,ev)
    assert not checks['real_brief']
    assert not checks['clean_title']
    assert not checks['search_tags']
    assert not checks['synthesized_points']


def test_grounding_uses_comment_and_frame_evidence():
    ev=dict(evidence(),comments=['The tool is Test Writer.'],frame_notes=['Tool Beta is visible.'])
    data=dict(payload(),entities={'tools_products':['Test Writer','Tool Beta'],'people_orgs':[],'numbers':[]})
    assert brief_v2.validate(data,ev).entities.tools_products==['Test Writer','Tool Beta']


def test_eval_detects_raw_sentence_inside_a_long_segment():
    ev=dict(evidence(),transcript=[{'start':5,'end':15,'text':'Okay, what else? Claude can plan the entire project. Then it checks its work.'}])
    assert brief_v2.copied_points([{'point':'Planning: Claude can plan the entire project.','source_ref':'00:05'}],ev)


def test_stt_only_misheard_skill_name_is_not_a_named_entity():
    ev=dict(evidence(),transcript=[{'start':5,'end':15,'text':"I'm pickabla. It helps frontend work."}])
    bad=dict(payload(),entities={'tools_products':['Pickabla'],'people_orgs':[],'numbers':[]})
    with pytest.raises(ValueError): brief_v2.validate(bad,ev)
    supported=dict(ev,ocr_text='PICKABLA')
    assert brief_v2.validate(bad,supported).entities.tools_products==['Pickabla']


def test_builtin_skill_availability_must_be_grounded():
    data=dict(payload(),instant_brief='Claude has built-in coding skills.')
    with pytest.raises(ValueError,match='Built-in availability'):
        brief_v2.validate(data,evidence())
    assert brief_v2.validate(data,dict(evidence(),caption='Claude has built-in coding skills.'))


def test_name_repair_lists_only_written_confirmed_entities():
    class Gateway:
        calls=[]
        async def generate_json(self, system, user, **kwargs):
            self.calls.append(user)
            if len(self.calls)==1:
                return dict(payload(),entities={'tools_products':['Claude Code','Pickabla'],'people_orgs':[],'numbers':[]})
            assert 'Keep only these confirmed named entities: ["Claude Code"]' in user
            assert 'Pickabla' not in user
            assert '"allowed_timestamps": ["00:05"]' in user
            return payload()
    gateway=Gateway()
    assert asyncio.run(brief_v2.extract(evidence(),gateway)).entities.tools_products==['Claude Code']
    assert len(gateway.calls)==2


def test_supported_phrase_tags_and_underscore_noise():
    ev=dict(evidence(),caption='Claude skills improve coding.')
    result=brief_v2.validate(dict(payload(),tags=payload()['tags']+['how_to']),ev)
    assert 'claude skills' in result.tags
    assert 'agent skills' not in result.tags and 'how_to' not in result.tags
