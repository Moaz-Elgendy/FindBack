"""Brief v2 policy. Providers remain behind the existing gateway."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app import env
from app.schemas import BriefV2
from app.services.ai_gateway import get_gateway

PROMPT_VERSION = 'brief_v3.2'
SYSTEM_PROMPT = (Path(__file__).parent.parent / 'prompts' / 'brief_v3.txt').read_text()
FORBIDDEN = ("the video's spoken content was unavailable", 'only the caption was available',
             'this video discusses', 'this post is about')
BANNED_TAGS = {'interesting', 'useful', 'video content', 'saved', 'pending', 'memory'}
log = logging.getLogger('findback.brief')


def normalized(text: str) -> str:
    return ' '.join(re.findall(r'\w+', text.casefold()))


def bad_tag(tag: str) -> bool:
    from app.services.search import STOPWORDS
    noise = STOPWORDS | set('every should most people only scratch surface what can five help automatically right before across know unlock reactions comments original audio see more how'.split())
    words = normalized(tag).replace("_", " ").split()
    return not words or words[0] in noise or tag in BANNED_TAGS


def copied_points(points: list[dict], evidence: dict) -> bool:
    sentences = {normalized(part) for s in evidence.get('transcript', [])
                 for part in [s['text'], *re.split(r'[.!?]+', s['text'])]}
    return any(normalized(part) in sentences and len(normalized(part).split()) >= 3
               for p in points for part in [p['point'], p['point'].split(':',1)[-1]])


def evidence_text(evidence: dict) -> str:
    return normalized(' '.join([str(evidence.get(k) or '') for k in ('title','author','caption','ocr_text')]
                              + [s['text'] for s in evidence.get('transcript', [])]
                              + list(evidence.get('comments', [])) + list(evidence.get('frame_notes', []))))



def clean_tags(values: list[str]) -> list[str]:
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError('Tags must be a list of strings')
    out = []
    for value in values:
        tag = re.sub(r'\s+', ' ', value.strip().lower())
        tag = re.sub(r'\bcalude\b', 'claude', tag)
        if tag and len(tag) <= 80 and not bad_tag(tag) and 1 <= len(tag.split()) <= 3 and tag not in out:
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
    corpus = evidence_text(evidence)
    phrases = [phrase for phrase in ('claude skills', 'agent skills') if phrase in corpus]
    data['tags'] = clean_tags(phrases + data.get('tags', []))
    brief = BriefV2.model_validate(data)
    if copied_points(data.get('key_points', []), evidence):
        raise ValueError('Points must synthesize facts, not copy transcript sentences')
    written = evidence_text(dict(evidence, transcript=[]))
    output_text = normalized(json.dumps(data, ensure_ascii=False))
    if 'built in' in output_text and 'built in' not in corpus:
        raise ValueError('Built-in availability is not established by source evidence')
    for name in ('anthropic','github','telegram'):
        if name in output_text.split() and name not in corpus.split():
            raise ValueError('Claim mentions a name absent from source evidence')
    if 'comment' in output_text.split() and 'comment' not in corpus.split():
        raise ValueError('Comment action is absent from source evidence')
    for name in [*brief.entities.tools_products, *brief.entities.people_orgs]:
        if normalized(name) not in written:
            raise ValueError('Named entity requires written-source confirmation, not an STT guess')
    for tag in brief.tags:
        if tag in {'anthropic','github','github repos','telegram','openai','claude mem','superpowers','impeccable','task observer'} and normalized(tag) not in corpus:
            raise ValueError('Named tag is not supported by evidence')
        if tag in {'claude skills','agent skills'}:
            words=set(corpus.split())
            if tag=='claude skills' and not ({'claude','skills'} <= words or 'claude' in words and 'مهارات' in words):
                raise ValueError('Skill phrase tag is unsupported')
            if tag=='agent skills' and not ({'agent','skills'} <= words or bool({'مهارات','مهارة'} & words) and bool({'وكلاء','وكيل'} & words)):
                raise ValueError('Agent skill phrase tag is unsupported')
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
    if evidence.get('evidence_level') == 'full_transcript':
        supplied['allowed_timestamps'] = list(dict.fromkeys(timestamp(s['start']) for s in evidence.get('transcript', [])))
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
            error = 'Validation failed; check required fields, counts, confidence, hedging, source timestamps, grounded names/tags and synthesized points.'
            if type(exc) is ValueError:
                error += ' ' + str(exc)
                if str(exc) == 'Named entity requires written-source confirmation, not an STT guess':
                    written = evidence_text(dict(evidence, transcript=[]))
                    confirmed = [name for key in ('tools_products', 'people_orgs')
                                 for name in data.get('entities', {}).get(key, []) if normalized(name) in written]
                    error += ' Keep only these confirmed named entities: ' + json.dumps(confirmed, ensure_ascii=False) + '. Remove every other name from entities, tags and prose; describe its function instead.'
            if evidence.get('evidence_level') == 'full_transcript':
                error += ' Each key point must use exactly one of these source_ref values: ' + json.dumps(supplied['allowed_timestamps']) + '. Omit caption-only key points; keep caption-only facts in instant_brief or suggested_action.'
            log.warning('Brief validation rejected: %s', error)
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
    """A pending placeholder: no invented summary, search handles or transcript bullets."""
    from app.schemas import Brief
    from app.services.fetcher import clean_source_text
    title = clean_source_text(evidence.get('title') or 'Saved link', title=True)[:80] or 'Saved link'
    arabic = bool(re.search(r'[\u0600-\u06ff]', evidence.get('caption') or title))
    data = dict(title=title, content_type='video' if evidence.get('transcript') else 'other',
                instant_brief=title, key_points=[], best_takeaway=None,
                entities={'tools_products': [], 'people_orgs': [], 'numbers': []}, topics=[], tags=[],
                search_phrases=[], likely_intent=None, suggested_action=None, confidence='low',
                evidence_used=[k for k,v in [('transcript',evidence.get('transcript')),('caption',evidence.get('caption')),
                    ('ocr',evidence.get('ocr_text')),('metadata',title)] if v],
                missing_info='تعذّر إنشاء الملخص؛ إعادة المحاولة معلّقة.' if arabic else 'Brief generation failed; a retry is pending.',
                brief_source='fallback', evidence_level=evidence.get('evidence_level','metadata_only'), prompt_version=PROMPT_VERSION)
    return data, Brief(title=title, overview=title)
