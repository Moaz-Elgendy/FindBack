"""Filters over saved intelligence, shared by library and search."""
import json
import re
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, Query
from sqlalchemy import exists, func, select
from app.models import Item

KEYS = {'topic', 'type', 'entity', 'intent', 'action', 'source', 'saved'}


# Shared with the mobile catalogue; labels are assigned by the LLM, not keywords.
TOPIC_LABELS = ('AI', 'Programming', 'Gym', 'Food', 'Electronics', 'Design', 'Business', 'Education', 'Science', 'History', 'Travel', 'Health', 'Finance', 'Entertainment', 'Lifestyle', 'Culture')
TOPIC_INSTRUCTIONS = (
    "TOPICS FOR FILTERING: Classify the actual subject semantically. Choose one or two broad labels from "
    + json.dumps(list(TOPIC_LABELS)) + ". Return these exact labels in topics. "
    "Use the content's main subject, not incidental keywords, a creator's name, a tool name, "
    "an intent, or a unique per-memory phrase. Use AI for AI systems/agents, Programming for "
    "software development, Gym for exercise/fitness, Food for recipes/cooking/nutrition. "
    "Use other catalogue subjects where supported, e.g. History for historical material and "
    "Science for astronomy. Never emit Other or guess a subject without meaningful evidence."
)


def semantic_topics(values):
    if not isinstance(values, list) or len(values) > 2:
        raise ValueError("topics must contain at most two broad semantic labels")
    labels = []
    for value in values:
        canonical = next((label for label in TOPIC_LABELS if isinstance(value, str) and label.casefold() == value.strip().casefold()), None)
        if canonical is None:
            raise ValueError("topics must use supported semantic labels, not specific phrases or Other")
        if canonical not in labels:
            labels.append(canonical)
    return labels


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
    q = db.query(Item).filter(Item.user_id == user_id, Item.deleted_at.is_(None))
    for key, value in values.items():
        if key in ('topic', 'entity'):
            entries = (func.jsonb_path_query_array(func.coalesce(
                           Item.brief_v2['topics'], Item.fetch_metadata['brief']['topics']), '$[*]')
                       if key == 'topic' else
                       func.jsonb_path_query_array(Item.entities, '$.tools_products[*]').op('||')(
                           func.jsonb_path_query_array(Item.entities, '$.people_orgs[*]')))
            labels = func.jsonb_array_elements_text(entries).table_valued('value')
            match = (func.lower(labels.c.value) == value.strip().lower() if key == 'topic' else
                     labels.c.value.op('~*')(r'(^|[^[:alnum:]_])' + re.escape(value.strip()) + r'($|[^[:alnum:]_])'))
            q = q.filter(exists(select(1).select_from(labels).where(match)))
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
