import asyncio
import json


def test_visual_evidence_is_bounded_and_uses_shared_provider_path(tmp_path, monkeypatch):
    from app.services import ai
    from app.services.scene_understanding import describe_frames
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key-not-a-real-key')
    monkeypatch.setenv('VISION_AI_PROVIDER', 'gemini')
    monkeypatch.delenv('VISION_AI_MODEL', raising=False)
    monkeypatch.setenv('EXTRACTOR_MODEL', 'gemini-3.8-flash')
    frames = []
    for i in range(6):
        path = tmp_path / f'{i}.jpg'
        path.write_bytes(b'jpeg fixture')
        frames.append(path)
    calls = []
    async def respond(url, headers, body, *, provider):
        calls.append((provider, body))
        return {'choices': [{'message': {'content': json.dumps({'scene_notes': ['A person wipes a table.']})}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 20}}
    monkeypatch.setattr(ai, '_post_json', respond)
    notes, metadata = asyncio.run(describe_frames(frames, 'A short cleaning demonstration'))
    assert notes == ['A person wipes a table.']
    assert metadata['vision_provider'] == 'gemini'
    assert metadata['vision_model'] == ai.chat_model('gemini')
    assert len(calls) == 1
    assert sum(part['type'] == 'image_url' for part in calls[0][1]['messages'][1]['content']) == 3


def test_scene_observations_do_not_confirm_invented_tool_names():
    from app.services import brief_v2
    from test_brief_v2 import payload, evidence
    import pytest
    data = payload()
    data['instant_brief'] = 'Claude uses ImaginaryTool to finish the project.'
    data['entities']['tools_products'].append('ImaginaryTool')
    with pytest.raises(ValueError, match='STT guess'):
        brief_v2.validate(data, dict(evidence(), visual_notes=['ImaginaryTool appears on a screen.']))
