import asyncio
import json
import pytest
from app.services import brief_v2
from test_brief_v2 import payload


def claude_mem_evidence():
    return dict(source_platform='facebook',url='https://facebook.com/reel/test',
                title='Claude Mem',caption='Claude Mem remembers project context.',
                evidence_level='full_transcript',transcript=[
                    {'start':24.24,'end':39.28,'text':'Plan an implementation before writing any code.'},
                    {'start':39.28,'end':48.18,'text':'Claude Mem remembers the project context across coding sessions.'}])


def claude_mem_payload(ids):
    return dict(payload(),entities={'tools_products':['Claude Mem'],'people_orgs':[],'numbers':[]},
                key_points=[{'point':'Claude Mem: retains context between coding sessions.',
                             'segment_ids':ids,'source_ref':'00:24'}])


def test_claude_mem_timestamp_is_computed_from_cited_segment():
    result=brief_v2.validate(claude_mem_payload([2]),claude_mem_evidence())
    assert result.key_points[0].source_ref=='00:39'
    assert result.key_points[0].segment_ids==[2]


@pytest.mark.parametrize('ids',[None,[],[99],['2'],[True]])
def test_missing_unknown_or_noninteger_id_is_repaired(ids):
    class Gateway:
        calls=[]
        async def generate_json(self,system,user,**kwargs):
            self.calls.append(user)
            supplied=json.loads(user.split('\nRepair',1)[0])
            assert supplied['transcript']==[
                {'id':1,'start':24.24,'text':'Plan an implementation before writing any code.'},
                {'id':2,'start':39.28,'text':'Claude Mem remembers the project context across coding sessions.'}]
            return claude_mem_payload(ids if len(self.calls)==1 else [2])
    gateway=Gateway()
    result=asyncio.run(brief_v2.extract(claude_mem_evidence(),gateway))
    assert len(gateway.calls)==2
    assert 'segment' in gateway.calls[1].lower()
    assert result.key_points[0].source_ref=='00:39'


def test_free_timestamp_without_id_is_rejected():
    data=claude_mem_payload([2])
    data['key_points'][0].pop('segment_ids')
    with pytest.raises(ValueError,match='segment'):
        brief_v2.validate(data,claude_mem_evidence())


def test_multiple_segment_ids_use_earliest_actual_start():
    result=brief_v2.validate(claude_mem_payload([2,1]),claude_mem_evidence())
    assert result.key_points[0].source_ref=='00:24'


def test_partial_evidence_cannot_accept_a_free_timestamp():
    data=claude_mem_payload([])
    with pytest.raises(ValueError,match='segment'):
        brief_v2.validate(data,dict(claude_mem_evidence(),evidence_level='partial'))


def test_production_rejections_record_repairs_with_reasons():
    from app.services import ai
    class Gateway:
        count=0
        async def generate_json(self,*args,**kwargs):
            self.count+=1
            return claude_mem_payload([99] if self.count==1 else [2])
    usage={}
    token=ai.CHAT_USAGE.set(usage)
    try:
        result=asyncio.run(brief_v2.extract(claude_mem_evidence(),Gateway()))
    finally:
        ai.CHAT_USAGE.reset(token)
    assert result.key_points[0].source_ref=='00:39'
    assert len(usage['validation_rejections'])==1
    rejection=usage['validation_rejections'][0]
    assert rejection['reason']=='timestamp' and rejection['attempt']==1
    assert 'Unknown segment id' in rejection['detail']


def test_supported_caption_and_ocr_names_are_accepted():
    data=claude_mem_payload([2])
    data['entities']['tools_products']=['Claude-Mem','IMPECCABLE']
    result=brief_v2.validate(data,dict(claude_mem_evidence(),ocr_text='IMPECCABLE'))
    assert result.entities.tools_products==['Claude-Mem','IMPECCABLE']


def test_tag_count_rejection_is_reported_even_when_repair_succeeds():
    from app.services import ai
    class Gateway:
        count=0
        async def generate_json(self,*args,**kwargs):
            self.count+=1
            return dict(claude_mem_payload([2]),tags=['coding']) if self.count==1 else claude_mem_payload([2])
    usage={}
    token=ai.CHAT_USAGE.set(usage)
    try:
        result=asyncio.run(brief_v2.extract(claude_mem_evidence(),Gateway()))
    finally:
        ai.CHAT_USAGE.reset(token)
    assert result.key_points[0].source_ref=='00:39'
    assert [r['reason'] for r in usage['validation_rejections']]==['tag_count']


def test_evaluator_reports_production_rejections_and_fallback():
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[2]/'scripts/eval_brief.py'
    spec=importlib.util.spec_from_file_location('eval_brief_ids',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result=asyncio.run(module.run(claude_mem_evidence(),claude_mem_payload([99])))
    assert result['output']['brief_source']=='fallback'
    assert not result['checks']['real_brief']
    assert [r['reason'] for r in result['rejections']]==['timestamp','timestamp']
