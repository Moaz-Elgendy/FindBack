"""Bounded visual evidence for short/silent videos, using the existing AI transport."""
import base64
from pathlib import Path

from app import env
from app.services import ai
from app.services.capacity import CapacityPause


async def describe_frames(frames: list[Path], caption: str) -> tuple[list[str], dict]:
    provider = env.get('VISION_AI_PROVIDER', 'gemini')
    if provider not in ai.CHAT_KEY_ENVS:
        return [], {'vision_unavailable': True}
    _, key = env.first_env(*ai.CHAT_KEY_ENVS[provider])
    if not key or not frames:
        return [], {'vision_unavailable': True}
    model = env.get('VISION_AI_MODEL', ai.chat_model(provider) if provider == 'gemini' else 'gpt-4o-mini')
    cfg = ai.ChatConfig(provider, key, model, f'{ai._chat_base_url(provider)}/chat/completions')
    selected = [frames[round(i * (len(frames) - 1) / min(2, len(frames) - 1))]
                for i in range(min(3, len(frames)))] if len(frames) > 1 else frames
    content = [{'type': 'text', 'text': 'Caption (context only): ' + caption[:1000]}]
    for path in selected:
        if path.stat().st_size > 500000:
            continue
        content.append({'type': 'image_url', 'image_url': {
            'url': 'data:image/jpeg;base64,' + base64.b64encode(path.read_bytes()).decode()}})
    if len(content) == 1:
        return [], {'vision_unavailable': True}
    body = {'model': model, 'temperature': 0, 'max_tokens': 500,
            'messages': [{'role': 'system', 'content':
                'Return JSON {"scene_notes": [short factual observations]}. Describe only visible '
                'objects, actions and scene changes. Use the caption language if clear, otherwise English. '
                'Do not infer identities, proper names, expertise, intent, causes or unseen events. '
                'Do not transcribe guessed text. Omit uncertain details. No platform navigation or extraction commentary.'},
                {'role': 'user', 'content': content}], 'response_format': {'type': 'json_object'}}
    try:
        response = await ai._post_json(cfg.url, cfg.headers, body, provider=provider)
    except ai.AIHTTPError as exc:
        if exc.status in (429, 503):
            raise CapacityPause(300, 'Visual provider temporarily unavailable') from exc
        raise
    result = ai.parse_json_object(ai.completion_text(response, provider), provider)
    notes = result.get('scene_notes', [])
    if not isinstance(notes, list):
        raise ValueError('Visual observations must be a list')
    usage = response.get('usage') or {}
    return [note.strip()[:400] for note in notes if isinstance(note, str) and note.strip()][:4], {
        'vision_provider': provider, 'vision_model': model, 'vision_frames': len(content) - 1,
        'vision_input_tokens': usage.get('prompt_tokens'), 'vision_output_tokens': usage.get('completion_tokens')}
