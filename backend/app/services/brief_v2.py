"""Brief v2 policy. Providers remain behind the existing gateway."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app import env
from app.schemas import BriefV2
from app.services.ai_gateway import get_gateway

PROMPT_VERSION = 'brief_v2'
SYSTEM_PROMPT = (Path(__file__).parent.parent / 'prompts' / 'brief_v2.txt').read_text()
FORBIDDEN = ("the video's spoken content was unavailable", 'only the caption was available',
             'this video discusses', 'this post is about')
BANNED_TAGS = {'interesting', 'useful', 'video content', 'saved', 'pending', 'memory'}
log = logging.getLogger('findback.brief')


def clean_tags(values: list[str]) -> list[str]:
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError('Tags must be a list of strings')
    out = []
    for value in values:
        tag = re.sub(r'\s+', ' ', value.strip().lower())
        tag = re.sub(r'\bcalude\b', 'claude', tag)
        if tag and len(tag) <= 80 and tag not in BANNED_TAGS and 1 <= len(tag.split()) <= 3 and tag not in out:
            out.append(tag)
    return out[:30]


def timestamp(start: float) -> str:
    seconds = int(start)
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


def model_input(evidence: dict) -> dict:
    # Explicit allowlist: failures and internal processing data cannot become content.
    keys = ('source_platform', 'url', 'source_id', 'title', 'author', 'duration',
            'caption', 'transcript', 'ocr_text', 'frame_notes', 'comments', 'evidence_level', 'language')
    return {k: evidence[k] for k in keys if k in evidence}


def validate(data: dict, evidence: dict) -> BriefV2:
    data = dict(data)
    data['tags'] = clean_tags(data.get('tags', []))
    brief = BriefV2.model_validate(data)
    encoded = json.dumps(brief.model_dump(), ensure_ascii=False).lower()
    if any(phrase in encoded for phrase in FORBIDDEN):
        raise ValueError('Forbidden extraction commentary')
    if brief.likely_intent and not (brief.likely_intent.startswith('You may have saved this to')
                                  or re.match(r'^(قد|ربما|لعل)', brief.likely_intent)):
        raise ValueError('likely_intent must be hedged')
    full = evidence.get('evidence_level') == 'full_transcript'
    if full:
        allowed = {timestamp(s['start']) for s in evidence.get('transcript', [])}
        if any(p.source_ref not in allowed for p in brief.key_points):
            raise ValueError('Every full-transcript point needs a segment start timestamp')
    elif brief.confidence == 'high' or not brief.missing_info:
        raise ValueError('Limited evidence requires low/medium confidence and missing_info')
    available = {'metadata'}
    if evidence.get('transcript'): available.add('transcript')
    if evidence.get('caption'): available.add('caption')
    if evidence.get('ocr_text') or evidence.get('frame_notes'): available.add('ocr')
    if set(brief.evidence_used) - available:
        raise ValueError('evidence_used names unavailable evidence')
    return brief


async def _generate(evidence: dict, gateway, *, reduced: list[dict] | None = None) -> BriefV2:
    supplied = model_input(evidence)
    if reduced is not None:
        supplied.pop('transcript', None)
        supplied['extracted_chunks'] = reduced
        supplied['allowed_timestamps'] = list(dict.fromkeys(
            p['source_ref'] for chunk in reduced for p in chunk['key_points'] if p['source_ref']))
    user = json.dumps(supplied, ensure_ascii=False)
    # Bound metadata and reduction overhead too; oversized input uses the grounded fallback.
    if len(user) > max(1000, env.get_int('BRIEF_CHUNK_CHARS', 12000)) + 12000:
        raise ValueError('Brief input exceeds bounded request budget')
    error = ''
    for attempt in range(2):
        instruction = user if not attempt else user + '\nRepair the previous response to match the schema. ' + error
        from app.services.ai_gateway import Gateway
        options = {"model_role": "complex" if reduced is not None else "normal"} if isinstance(gateway, Gateway) else {}
        data = await gateway.generate_json(SYSTEM_PROMPT, instruction, temperature=0.0, **options)
        try:
            result = validate(data, evidence)
            if reduced is not None and evidence.get('evidence_level') == 'full_transcript':
                if any(p.source_ref not in supplied['allowed_timestamps'] for p in result.key_points):
                    raise ValueError('Merge introduced an unavailable timestamp')
            return result
        except (ValueError, TypeError) as exc:
            # Do not repeat invalid model text or fetch errors as model content.
            error = 'Validation failed; check required fields, counts, confidence, hedging and source timestamps.'
            if attempt:
                raise ValueError(error) from exc
    raise ValueError(error)


async def extract(evidence: dict, gateway=None) -> BriefV2:
    gateway = gateway or get_gateway()
    segments = evidence.get('transcript') or []
    cap = max(1000, env.get_int('BRIEF_CHUNK_CHARS', 12000))
    groups, current, size = [], [], 2
    split_segments = []
    for segment in segments:
        if len(json.dumps(segment, ensure_ascii=False)) + 4 <= cap:
            split_segments.append(segment)
            continue
        text = segment['text']
        # Keep original timestamps; splitting text must not invent finer timing.
        width = max(1, cap // 2 - 128)
        split_segments.extend(dict(segment, text=text[i:i+width]) for i in range(0, len(text), width))
    for segment in split_segments:
        length = len(json.dumps(segment, ensure_ascii=False))
        if current and size + length > cap:
            groups.append(current)
            current, size = [], 2
        current.append(segment)
        size += length + 2
    if current: groups.append(current)
    if len(groups) <= 1:
        return await _generate(dict(evidence, transcript=groups[0]) if groups else evidence, gateway)
    reduced = []
    for group in groups:
        part = dict(evidence, transcript=group)
        result = await _generate(part, gateway)
        reduced.append(result.model_dump())
    # Pairwise reduction bounds fan-in regardless of video length.
    while len(reduced) > 1:
        merged = []
        for i in range(0, len(reduced), 2):
            pair = reduced[i:i+2]
            if len(pair) == 1:
                merged.append(pair[0])
                continue
            compact = [{k: chunk[k] for k in ('title', 'instant_brief', 'key_points', 'entities')}
                       for chunk in pair]
            result = await _generate(evidence, gateway, reduced=compact)
            merged.append(result.model_dump())
        reduced = merged
    return validate(reduced[0], evidence)


def legacy(brief: BriefV2):
    from app.schemas import Brief
    return Brief(title=brief.title, overview=brief.instant_brief,
                 highlights=[p.point for p in brief.key_points],
                 entities=[*brief.entities.tools_products, *brief.entities.people_orgs, *brief.entities.numbers],
                 topics=brief.tags, actions=[brief.suggested_action] if brief.suggested_action else [],
                 timestamps=[p.source_ref for p in brief.key_points if p.source_ref],
                 structured_data={'content_type': brief.content_type})


def offline(evidence: dict) -> tuple[dict, object]:
    """Grounded fallback. Sparse evidence is never padded to satisfy tag counts."""
    from app.schemas import Brief
    from app.services.search import STOPWORDS
    transcript = evidence.get('transcript') or []
    title = evidence.get('title') or 'Saved link'
    body = evidence.get('ocr_text') or evidence.get('caption') or title
    points = [{'point': s['text'], 'source_ref': timestamp(s['start'])} for s in transcript]
    if not points and body != title:
        points = [{'point': line.strip(), 'source_ref': 'ocr' if evidence.get('ocr_text') else 'caption'}
                  for line in body.splitlines() if line.strip()][:25]
    corpus = ' '.join([title, body, *[s['text'] for s in transcript]])
    words = re.findall(r'[^\W_]+(?:[-+][^\W_]+)*', corpus.lower())
    candidates = [w for w in words if w not in STOPWORDS and len(w) > 1]
    candidates.extend(' '.join(words[i:i+n]) for n in (2, 3) for i in range(len(words)-n+1)
                      if words[i] not in STOPWORDS and words[i+n-1] not in STOPWORDS)
    if evidence.get('source_platform'): candidates.append(evidence['source_platform'])
    full = evidence.get('evidence_level') == 'full_transcript'
    text = ' '.join(s['text'] for s in transcript[:3]) if transcript else body
    tags = clean_tags(candidates)
    data = dict(title=title[:80], content_type='video' if transcript else 'other',
                instant_brief=text[:600], key_points=points, best_takeaway=None,
                entities={'tools_products': [], 'people_orgs': [], 'numbers': []}, topics=[], tags=tags,
                search_phrases=[], likely_intent=None, suggested_action=None,
                confidence='medium' if full or evidence.get('caption') or evidence.get('ocr_text') else 'low',
                evidence_used=[k for k, v in [('transcript', transcript), ('caption', evidence.get('caption')),
                               ('ocr', evidence.get('ocr_text')), ('metadata', title)] if v],
                missing_info=None if full else ('افتح المصدر الأصلي للتفاصيل.' if re.search(r'[\u0600-\u06ff]', body)
                                                else 'Open original for the remaining details.'))
    compatible = Brief(title=data['title'], overview=data['instant_brief'],
                       highlights=[p['point'] for p in points], topics=tags,
                       timestamps=[p['source_ref'] for p in points if p['source_ref']])
    return data, compatible
