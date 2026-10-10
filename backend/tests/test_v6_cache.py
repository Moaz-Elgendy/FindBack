from datetime import datetime, timedelta, timezone
import uuid
import pytest
from sqlalchemy import text
from test_phase16_multitenant import admin_engine, db, sessions, client, two_users
from app.models import Item, ContentAsset
from app.services import public_cache


def ready_public(session, item_id):
    item = session.get(Item, uuid.UUID(item_id))
    item.title_clean = 'Anonymous public title'
    item.summary = 'Anonymous public summary'
    from test_brief_v2 import payload
    item.brief_v2 = dict(payload(), brief_source='llm', title=item.title_clean, instant_brief=item.summary)
    from app.services import embedder
    item.embedding_model = embedder.embedding_model_name()
    item.needs_retry = False
    item.processing_metadata = {'anonymous_source': True}
    from app.schemas import BriefV2
    from app.services import brief_v2
    item.fetch_metadata = {'brief': brief_v2.legacy(BriefV2(**dict(payload(), title=item.title_clean, instant_brief=item.summary))).model_dump()}
    return item


def test_cache_survives_first_personal_copy_deletion(sessions, two_users):
    with sessions() as s:
        item = ready_public(s, two_users['a_item'])
        asset = public_cache.publish(s, item)
        assert asset.owner_user_id is None
        asset_id = asset.id
        url = item.url
        s.commit()
        s.delete(item)
        s.commit()
        hit = public_cache.lookup(s, url)
        assert hit.id == asset_id
        other = s.get(Item, uuid.UUID(two_users['b_item']))
        assert public_cache.apply(s, other, hit)
        assert other.summary == 'Anonymous public summary'
        assert other.title_clean == 'Anonymous public title'
        assert other.user_id == two_users['b']


def test_ttl_is_sliding_and_expiry_is_authoritative(sessions, two_users):
    with sessions() as s:
        item = ready_public(s, two_users['a_item'])
        asset = public_cache.publish(s, item)
        url = item.url
        now = datetime.now(timezone.utc)
        asset.cache_expires_at = now + timedelta(seconds=2)
        s.commit()
        assert public_cache.lookup(s, url, now=now).id == asset.id
        assert asset.cache_expires_at == now + timedelta(days=30)
        assert asset.cache_last_hit_at == now
        s.commit()
        assert public_cache.lookup(s, url, now=now + timedelta(days=31)) is None


def test_cleanup_removes_only_global_payload(sessions, two_users):
    with sessions() as s:
        item = ready_public(s, two_users['a_item'])
        asset = public_cache.publish(s, item)
        asset.cache_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.commit()
        assert public_cache.cleanup(s) == 1
        s.refresh(item)
        s.refresh(asset)
        assert item.summary == 'Anonymous public summary'
        assert item.brief_v2['brief_source'] == 'llm'
        assert asset.cache_payload is None
        assert asset.brief is None
        assert public_cache.lookup(s, item.url) is None


@pytest.mark.parametrize('changes', [
    {'needs_retry': True}, {'processing_metadata': {}},
    {'brief_v2': {'brief_source': 'fallback'}}, {'summary': ''},
])
def test_unusable_or_unverified_results_are_never_published(sessions, two_users, changes):
    with sessions() as s:
        item = ready_public(s, two_users['a_item'])
        for key, value in changes.items():
            setattr(item, key, value)
        assert public_cache.publish(s, item) is None
        assert public_cache.lookup(s, item.url) is None


def test_cached_output_excludes_edits_hints_notes_and_identity(sessions, two_users):
    with sessions() as s:
        item = ready_public(s, two_users['a_item'])
        item.edited_summary = 'Private edited text'
        item.raw_preview = 'Private clipboard hint'
        item.edited_title = 'Private name'
        asset = public_cache.publish(s, item)
        encoded = str(asset.cache_payload)
        assert 'Private' not in encoded
        assert str(item.user_id) not in encoded
        assert 'user_id' not in asset.cache_payload


@pytest.mark.parametrize('url,title,body', [
    ('https://facebook.com/posts/1', 'Log in to Facebook', 'Sign in to continue to view this content'),
    ('https://youtube.com/watch?v=dQw4w9WgXcQ', 'Private video', 'This video is private'),
    ('https://example.com/page?access_token=secret', 'An accessible page', 'Public looking text'),
    ('https://example.com/page?api_key=secret', 'An accessible page', 'A credential-protected article body that looks public and has useful content.'),
    ('https://example.com/page?authorization=secret', 'An accessible page', 'A credential-protected article body that looks public and has useful content.'),
    ('https://example.com/page?client_secret=secret', 'An accessible page', 'A credential-protected article body that looks public and has useful content.'),
    ('https://example.com/page?credential=secret', 'An accessible page', 'A credential-protected article body that looks public and has useful content.'),
    ('https://example.com/page?X-Amz-Signature=secret', 'An accessible page', 'A credential-protected article body that looks public and has useful content.'),
])
def test_login_walled_or_credential_links_are_not_public(url, title, body):
    assert not public_cache.anonymous_usable(url, {'title': title, 'text': body, 'input_provenance': 'page'})


@pytest.mark.parametrize('url', ['https://facebook.com/posts/1', 'https://youtube.com/watch?v=dQw4w9WgXcQ'])
def test_genuine_anonymous_content_can_be_public(url):
    assert public_cache.anonymous_usable(url, {'title': 'Public tutorial', 'text': 'Learn how to grow vegetables using these four practical steps.', 'input_provenance': 'page'})


def test_cache_migration_roundtrip_preserves_personal_rows(db, sessions, two_users):
    from alembic import command
    from app import database
    cfg = database.alembic_config()
    cfg.set_main_option('sqlalchemy.url', db.url.render_as_string(hide_password=False))
    query = text('SELECT id, user_id, url, summary FROM items ORDER BY id')
    with db.connect() as connection:
        before = connection.execute(query).all()
    try:
        command.downgrade(cfg, '0025_snapshot_reconciliation')
        with db.connect() as connection:
            assert connection.execute(query).all() == before
        command.upgrade(cfg, 'head')
        with db.connect() as connection:
            assert connection.execute(query).all() == before
    finally:
        command.upgrade(cfg, 'head')


def test_url_lock_and_pipeline_commits_share_one_connection(db, two_users):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    engine = create_engine(db.url, pool_size=1, max_overflow=0, pool_timeout=0.2)
    try:
        with Session(engine) as session:
            item = session.get(Item, uuid.UUID(two_users['a_item']))
            from app.utils.canonical import canonical_url
            args = {'url': canonical_url(item.url)}
            with public_cache.serialized(session, item.url):
                for _ in range(2):
                    assert session.execute(text('SELECT 1')).scalar() == 1
                    session.commit()
                    with db.connect() as observer:
                        assert not observer.execute(text(
                            'SELECT pg_try_advisory_lock(hashtextextended(:url, 0))'), args).scalar()
            with db.connect() as observer:
                assert observer.execute(text(
                    'SELECT pg_try_advisory_lock(hashtextextended(:url, 0))'), args).scalar()
                observer.execute(text(
                    'SELECT pg_advisory_unlock(hashtextextended(:url, 0))'), args)
                observer.commit()
    finally:
        engine.dispose()
