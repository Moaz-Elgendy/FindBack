"""Durable previous brief for explicitly requested processing."""
FIELDS = ('title', 'title_clean', 'summary', 'key_points', 'category', 'entities',
          'intent', 'tags', 'brief_v2', 'evidence_bundle', 'fetch_metadata',
          'processing_metadata', 'needs_retry', 'search_text', 'source_type',
          'thumbnail_url', 'embedding_model', 'link_only')


def edited(item):
    return item.edited_title is not None or item.edited_summary is not None


def snapshot(item, replace_edits):
    return dict({name: getattr(item, name) for name in FIELDS}, replace_edits=replace_edits)


def fail(item):
    if not item.reprocess_snapshot:
        return False
    for name in FIELDS:
        setattr(item, name, item.reprocess_snapshot.get(name))
    item.status = 'ready'
    item.failure_reason = None
    item.reprocess_snapshot = None
    item.reprocess_failure = 'This page could not be read. Your previous brief was kept.'
    return True


def succeed(item):
    if item.reprocess_snapshot and item.reprocess_snapshot.get('replace_edits'):
        item.edited_title = None
        item.edited_summary = None
    item.reprocess_snapshot = None
    item.reprocess_failure = None
