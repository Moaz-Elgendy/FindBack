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


def detach(db, item):
    import uuid
    from app.models import ContentAsset, UserMemory, Item, VISIBILITY_UNKNOWN
    from app.utils.canonical import canonical_url
    previous = item.content_id
    asset = ContentAsset(canonical_url=canonical_url(item.url),
                         dedupe_key='regeneration:' + str(uuid.uuid4()),
                         owner_user_id=item.user_id, visibility=VISIBILITY_UNKNOWN,
                         title=item.title_clean or item.title, brief=item.summary)
    db.add(asset)
    db.flush()
    memory = (db.query(UserMemory).filter(UserMemory.content_id == previous,
              UserMemory.user_id == item.user_id).with_for_update().first()) if previous else None
    others = db.query(Item).filter(Item.content_id == previous, Item.user_id == item.user_id,
                                    Item.id != item.id).first() if previous else None
    values = {field: getattr(memory, field) for field in
              ('user_note', 'user_intent', 'first_saved_at', 'last_saved_at', 'save_count', 'created_at', 'updated_at')} if memory else {}
    db.add(UserMemory(user_id=item.user_id, content_id=asset.id, **values))
    db.flush()
    item.content_id = asset.id
    item.processing_metadata = dict(item.processing_metadata or {}, anonymous_source=False)
    db.flush([item])
    if memory is not None and others is None:
        db.delete(memory)
    return asset
