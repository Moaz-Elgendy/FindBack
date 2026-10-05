#!/usr/bin/env python3
"""Evaluate actual Brief stages using fixed evidence; network only with --live/--url."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.services import ai_gateway, brief_v2, fetcher, media_understanding, pipeline


class FixtureAdapter:
    name = 'fixture'
    def __init__(self, output): self.output = output
    async def generate_json(self, system, user, **kwargs):
        assert system == brief_v2.SYSTEM_PROMPT
        assert 'fetch_errors' not in json.loads(user)
        return self.output


def checks(output: dict, evidence: dict) -> dict[str, bool]:
    text = json.dumps(output, ensure_ascii=False).lower()
    full = evidence['evidence_level'] == 'full_transcript'
    return {
        'no_meta_phrases': not any(p in text for p in brief_v2.FORBIDDEN),
        'tag_count_15_30': 15 <= len(output['tags']) <= 30,
        'timestamped_points': not full or all(p['source_ref'] in {
            brief_v2.timestamp(s['start']) for s in evidence['transcript']} for p in output['key_points']),
        'confidence_matches_evidence': output['confidence'] in ('high', 'medium') if full
                                       else output['confidence'] in ('low', 'medium'),
    }


async def run(evidence: dict, output: dict | None = None) -> dict:
    old = ai_gateway.set_gateway(ai_gateway.Gateway(FixtureAdapter(output))) if output else None
    try:
        item = SimpleNamespace(id='eval', url=evidence['url'], title=evidence['title'],
            raw_text=evidence.get('caption', ''), normalized_text=evidence.get('caption', ''),
            fetch_metadata={}, evidence_bundle=evidence, processing_metadata={})
        await pipeline.stage_understand(item)
        await pipeline.stage_brief(item)
        await pipeline.stage_chunk(item)
        result = item.brief_v2
        assert result.get('missing_info') is None or result['missing_info'] not in item.search_text
        return {'output': result, 'checks': checks(result, evidence),
                'processing_metadata': item.processing_metadata}
    finally:
        if old is not None: ai_gateway.set_gateway(old)


async def main(args) -> int:
    results = []
    if args.url:
        try:
            fetched = await fetcher.fetch_content(args.url)
        except Exception as exc:
            fetched = {'text': '', 'input_provenance': 'none', 'fetch_errors': [type(exc).__name__]}
        fetched['title'] = fetched.get('title') or args.title or ''
        caption = media_understanding.initial_bundle(args.url, dict(fetched, transcript=[]))
        results.append({'name': 'caption_only', 'evidence_level': caption.evidence_level,
                        **await run(caption.model_dump())})
        bundle, meta = await media_understanding.acquire(args.url, fetched)
        results.append({'name': 'full_pipeline', 'evidence_level': bundle.evidence_level,
                        'acquisition_metadata': meta, 'acquisition_errors': bundle.fetch_errors,
                        **await run(bundle.model_dump())})
    else:
        fixtures = json.loads((ROOT / 'backend/tests/fixtures/brief_v2.json').read_text())
        for fixture in fixtures:
            results.append({'name': fixture['name'], **await run(fixture['evidence'],
                           None if args.live else fixture['output'])})
    rendered = json.dumps(results, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output: Path(args.output).write_text(rendered + '\n')
    passed = sum(all(r['checks'].values()) for r in results)
    print(f'Evaluation: {passed}/{len(results)} cases passed', file=sys.stderr)
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Use configured LLM for fixtures')
    parser.add_argument('--url', help='Manually compare real caption-only and acquired media; uses network')
    parser.add_argument('--title', help='Optional trusted title hint when upstream metadata is unavailable')
    parser.add_argument('--output', help='Write operator evaluation JSON')
    raise SystemExit(asyncio.run(main(parser.parse_args())))
