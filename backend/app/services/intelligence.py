"""Filters over saved intelligence, shared by library and search."""
import json
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, Query
from sqlalchemy import Text, cast, func
from app.models import Item

KEYS = {'topic', 'type', 'entity', 'intent', 'action', 'source', 'saved'}


def read_filters(intelligence: str | None = Query(None, max_length=4096)) -> dict:
    if intelligence is None:
        return {}
    try:
        values = json.loads(intelligence)
        if not isinstance(values, dict) or set(values) - KEYS:
            raise ValueError()
        if any(not isinstance(v, str) or not v.strip() or len(v) > 512 for v in values.values()):
            raise ValueError()
        if 'saved' in values:
            datetime.strptime(values['saved'], '%Y-%m-%d')
        return values
    except (ValueError, TypeError):
        raise HTTPException(422, 'Invalid intelligence filters') from None


def query_for(db, user_id, values):
    q = db.query(Item).filter(Item.user_id == user_id)
    for key, value in values.items():
        if key == 'topic':
            q = q.filter(func.lower(cast(Item.brief_v2['topics'], Text)).contains(
                json.dumps(value.lower(), ensure_ascii=False), autoescape=True))
        elif key == 'entity':
            entries = func.jsonb_path_query_array(Item.entities, '$.*[*]')
            q = q.filter(func.lower(cast(entries, Text)).contains(
                json.dumps(value.lower(), ensure_ascii=False), autoescape=True))
        elif key == 'saved':
            start = datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            q = q.filter(Item.created_at >= start, Item.created_at < start + timedelta(days=1))
        else:
            expression = {'type': Item.brief_v2['content_type'].astext,
                          'intent': func.coalesce(Item.brief_v2['likely_intent'].astext, Item.intent),
                          'action': Item.brief_v2['suggested_action'].astext,
                          'source': Item.source_domain}[key]
            q = q.filter(func.lower(expression) == value.lower())
    return q
