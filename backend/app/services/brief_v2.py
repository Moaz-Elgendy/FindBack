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
    out = []
    for value in values:
        tag = re.sub(r'\s+', ' ', value.strip().lower())
        tag = re.sub(r'\bcalude\b', 'claude', tag)
        if tag and tag not in BANNED_TAGS and 1 <= len(tag.split()) <= 3 and tag not in out:
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
        supplied['allowed_timestamps'] = [timestamp(s['start']) for s in evidence.get('transcript', [])]
    user = json.dumps(supplied, ensure_ascii=False)
    error = ''
    for attempt in range(2):
        instruction = user if not attempt else user + '\nRepair the previous response to match the schema. ' + error
        data = await gateway.generate_json(SYSTEM_PROMPT, instruction, temperature=0.0)
        try:
            return validate(data, evidence)
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
    groups, current, size = [], [], 0
    for segment in segments:
        length = len(json.dumps(segment, ensure_ascii=False))
        if current and size + length > cap:
            groups.append(current)
            current, size = [], 0
        current.append(segment)
        size += length
    if current: groups.append(current)
    if len(groups) <= 1:
        return await _generate(evidence, gateway)
    reduced = []
    for group in groups:
        part = dict(evidence, transcript=group)
        result = await _generate(part, gateway)
        reduced.append(result.model_dump())
    # Timestamp references are checked against original segments after merging.
    return await _generate(evidence, gateway, reduced=reduced)


def legacy(brief: BriefV2):
    from app.schemas import Brief
    return Brief(title=brief.title, overview=brief.instant_brief,
                 highlights=[p.point for p in brief.key_points],
                 entities=[*brief.entities.tools_products, *brief.entities.people_orgs, *brief.entities.numbers],
                 topics=brief.tags, actions=[brief.suggested_action] if brief.suggested_action else [],
                 timestamps=[p.source_ref for p in brief.key_points if p.source_ref],
                 structured_data={'content_type': brief.content_type})
