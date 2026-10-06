#!/usr/bin/env python3
"""Seed labelled benchmark distractors and trace the unchanged production search."""
import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from sqlalchemy import func
from app.database import SessionLocal
from app.models import Item
from app.services import embedder, search

SEED = 'brief-segment-search-20261005'
DISTRACTORS = [
    ('video', 'Claude Code basics for developers', 'Install Claude Code, authenticate and ask the coding assistant to explain a project.', ['claude code','developers','ai coding','beginners']),
    ('video', 'CLAUDE.md project memory', 'Use a CLAUDE.md file to record project conventions and persistent instructions for Claude Code.', ['claude code','project memory','coding conventions','ai assistant']),
    ('video', 'Claude Code planning mode', 'Review an implementation plan before allowing Claude Code to edit or test application code.', ['claude code','planning','developers','testing']),
    ('video', 'Claude frontend design prompts', 'Prompt Claude to build a frontend with clear typography, layouts and accessible components.', ['claude','frontend design','ui','coding']),
    ('video', 'Claude Code custom agent skills', 'Create reusable agent skills for a Claude Code workflow and organise skill instructions.', ['claude code','agent skills','claude skills','ai workflow']),
    ('video', 'Installing Claude Mem', 'Install Claude Mem to remember previous coding sessions and retrieve project context.', ['claude mem','claude code','project memory','installation']),
    ('video', 'Superpowers planning workflow', 'Use Superpowers with Claude Code for brainstorming, planning, testing and code review.', ['superpowers','claude code','planning','testing']),
    ('video', 'ChatGPT Python debugging', 'Use ChatGPT to inspect Python tracebacks and propose unit tests for failed code.', ['chatgpt','python','debugging','unit tests']),
    ('video', 'Cursor refactoring tutorial', 'Use the Cursor editor to refactor a TypeScript project and review AI generated edits.', ['cursor','typescript','refactoring','ai editor']),
    ('video', 'GitHub Copilot editor tips', 'Use GitHub Copilot completion and chat inside an editor while writing application code.', ['github copilot','developers','ai coding','editor']),
    ('video', 'Local coding with Ollama', 'Run a local coding model through Ollama for offline code explanation and completion.', ['ollama','local models','ai coding','offline']),
    ('video', 'MCP server integrations', 'Connect an AI coding assistant to external tools using MCP servers and inspect tool permissions.', ['mcp','ai tools','coding assistant','integrations']),
    ('video', 'Gemini CLI coding workflow', 'Use Gemini CLI to explain repository code, generate tests and investigate bugs.', ['gemini','cli','developers','testing']),
    ('recipe', 'Crispy chicken and rice', 'Season chicken with paprika and salt, bake at 200 C for 25 minutes and serve with rice.', ['chicken','rice','recipe','dinner']),
    ('product', 'Mechanical keyboard for developers', 'A compact mechanical keyboard with tactile switches, USB C and a programmable layout.', ['keyboard','developers','usb c','product']),
    ('article', 'Postgres hybrid search explained', 'Combine full text keyword retrieval with vector embeddings and reciprocal rank fusion in PostgreSQL.', ['postgres','hybrid search','vectors','keyword search']),
]
QUERIES = ['that video about claude code skills for developers',
           'the AI clip that remembers projects',
           'the reel with planning memory and frontend tips']


async def main(args):
    with SessionLocal() as db:
        target = db.get(Item, args.item)
        if target is None:
            raise ValueError('Target item is absent from the configured database; no seed was written')
        ids = [uuid.uuid5(uuid.NAMESPACE_URL, SEED + str(target.user_id) + str(i)) for i in range(len(DISTRACTORS))]
        missing = [(i, row) for i, row in enumerate(DISTRACTORS) if db.get(Item, ids[i]) is None]
        texts = [' '.join([row[1], row[2], *row[3]]) for _, row in missing]
        vectors = await embedder.embed_many(texts)
        if len(vectors) != len(missing) or any(v is None for v in vectors):
            raise ValueError('Real distractor embeddings failed; no partial seed was written')
        for (i, (kind, title, summary, tags)), vector in zip(missing, vectors):
            url = 'https://brief-evaluation.invalid/' + str(ids[i])
            db.add(Item(id=ids[i], user_id=target.user_id, url=url, canonical_url=url,
                title=title, title_clean=title, summary=summary,
                source_type=kind, source_domain='brief-evaluation.invalid', category=kind,
                tags=tags, search_text=' '.join([title, summary, *tags]),
                embedding=vector, embedding_model=embedder.embedding_model_name(),
                status='ready', needs_retry=False, brief_v2={}, created_at=target.created_at,
                processing_metadata={'evaluation_seed':SEED,'synthetic':True}))
        db.commit()
        seeded = db.query(Item).filter(Item.user_id==target.user_id,Item.id.in_(ids)).count()
        assert seeded >= 15
        corpus = db.query(func.count(Item.id)).filter(Item.user_id==target.user_id,Item.status=='ready').scalar()
        results = []
        for query in QUERIES:
            captured = {}
            originals = {}
            # Capture the exact candidate lists used by production, without changing scores/order.
            for name in ('lexical_search','vector_search','chunk_lexical_search','note_search','_chunk_vector_rows'):
                originals[name] = getattr(search,name)
                def trace(*a,_name=name,_fn=originals[name],**kw):
                    rows=_fn(*a,**kw); captured[_name]=rows; return rows
                setattr(search,name,trace)
            try:
                hits,took=await search.hybrid_search(db,target.user_id,query,limit=50)
            finally:
                for name,fn in originals.items():setattr(search,name,fn)
            ranks=[str(hit['row'][0]) for hit in hits]
            match=next((hit for hit in hits if str(hit['row'][0])==str(target.id)),None)
            channels={}
            for name,rows in captured.items():
                found=next(((i,row) for i,row in enumerate(rows,1) if str(row[0])==str(target.id)),None)
                channels[name]={'surfaced':found is not None,
                    'candidate_rank':found[0] if found else None,
                    'raw_score':float(found[1][9]) if found and len(found[1])>9 else None}
            terms=search.correct_tag_terms(db,target.user_id,search.query_terms(query))
            results.append({'query':query,'rank':ranks.index(str(target.id))+1 if str(target.id) in ranks else None,
                'score':match['score'] if match else None,'returned':len(hits),'took_ms':took,
                'channels':channels,'tag_matches':search.matched_terms(terms,' '.join(target.tags or [])),
                'tag_channel':'tags are included in indexed lexical text; no independent tag candidate channel',
                'top5':[{'id':str(h['row'][0]),'title':h['row'][1],'score':h['score']} for h in hits[:5]]})
        rendered=json.dumps({'seed':SEED,'synthetic_distractors':seeded,'ready_corpus':corpus,
            'seed_created_at':'matched target created_at to avoid a synthetic recency advantage',
            'results':results},indent=2,ensure_ascii=False)
        print(rendered)
        if args.output:Path(args.output).write_text(rendered+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--item',required=True)
    parser.add_argument('--output')
    asyncio.run(main(parser.parse_args()))
