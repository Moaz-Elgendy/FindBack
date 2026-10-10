"""Explicit snapshot sharing; recipients never depend on sender-owned rows."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html import escape
import os
import re
import secrets
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import env
from app.auth import get_current_user
from app.database import get_db
from app.models import Item, Chunk, MemoryShare, ShareRedemption
from app.schemas import ItemDetail
from app.utils.url_safety import validate_url
from app.utils.canonical import source_domain, source_type

router = APIRouter(tags=['sharing'])


def registered_user(user=Depends(get_current_user)):
    from app.services.auth_tokens import GUEST_ISSUER
    if user.auth_provider == GUEST_ISSUER:
        raise HTTPException(401, 'Sign in to share or save a shared memory')
    return user


class Profile(BaseModel):
    model_config = ConfigDict(extra='forbid', from_attributes=True)
    display_name: str | None = Field(None, max_length=80)

    @field_validator('display_name')
    @classmethod
    def name(cls, value):
        value = value.strip() if value else None
        if value and ('@' in value or any(ord(c) < 32 for c in value)):
            raise ValueError('Use a name, not an email address')
        return value or None


@router.get('/api/v1/account/profile', response_model=Profile)
def profile(user=Depends(registered_user)):
    return user


@router.put('/api/v1/account/profile', response_model=Profile)
def update_profile(value: Profile, db: Session = Depends(get_db), user=Depends(registered_user)):
    user.display_name = value.display_name
    db.add(user)
    db.commit()
    return user


def lock_account(db, user):
    # The same lock covers creation/redemption quotas and concurrent duplicate saves.
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
               {'key': 'sharing:' + str(user.id)})


def active(db, token):
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise HTTPException(404, 'Share link not found')
    share = db.query(MemoryShare).filter(MemoryShare.token_hash == sha256(token.encode()).hexdigest()).first()
    if share is None:
        raise HTTPException(404, 'Share link not found')
    if share.revoked_at or share.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(410, 'This share link has expired or been revoked')
    return share


def metadata(share):
    return {'id': str(share.id), 'title': share.snapshot['title'],
            'created_at': share.created_at, 'expires_at': share.expires_at}


@router.post('/api/v1/items/{item_id}/share', status_code=201)
def create_share(item_id: UUID, db: Session = Depends(get_db), user=Depends(registered_user)):
    lock_account(db, user)
    item = db.query(Item).filter(Item.id == item_id, Item.user_id == user.id, Item.deleted_at.is_(None)).first()
    if item is None:
        raise HTTPException(404, 'Memory not found')
    if item.status != 'ready' or item.link_only or item.needs_retry:
        raise HTTPException(409, 'A ready summary is needed to share a memory. You can still share the original link')
    now = datetime.now(timezone.utc)
    if db.query(MemoryShare).filter(MemoryShare.user_id == user.id, MemoryShare.created_at > now - timedelta(days=1)).count() >= 20:
        raise HTTPException(429, 'You can create up to 20 share links per day', headers={'Retry-After': '86400'})
    detail = ItemDetail.model_validate(item)
    url = validate_url(item.canonical_url)
    if any(re.search(r'(?:token|secret|password|signature|credential|api.?key|authorization)', key, re.I) for key, _ in parse_qsl(urlsplit(url).query)):
        raise HTTPException(409, 'Use a public source URL without access credentials before sharing')
    points = detail.key_points_with_refs or [{'point': p} for p in detail.key_points]
    # Whitelist display content; no extraction, private notes/tags/hints or asset references.
    snapshot = {'url': url, 'title': detail.title_clean or detail.title,
                'summary': detail.instant_brief or detail.summary,
                'key_points': [{'point': p.get('point', ''), **({'source_ref': p['source_ref']} if re.fullmatch(r'\d+:[0-5]\d', str(p.get('source_ref', ''))) else {})} for p in points],
                'content_type': detail.content_type, 'shared_by': user.display_name}
    token = secrets.token_urlsafe(32)
    share = MemoryShare(user_id=user.id, token_hash=sha256(token.encode()).hexdigest(), snapshot=snapshot,
                        created_at=now, expires_at=now + timedelta(days=max(1, env.get_int('SHARE_LINK_TTL_DAYS', 30))))
    db.add(share)
    db.commit()
    origin = os.getenv('SHARE_LINK_ORIGIN', 'https://findback.duckdns.org').rstrip('/')
    return {**metadata(share), 'url': origin + '/s/' + token}


@router.get('/api/v1/account/shares')
def list_shares(db: Session = Depends(get_db), user=Depends(registered_user)):
    return {'shares': [metadata(s) for s in db.query(MemoryShare).filter(MemoryShare.user_id == user.id,
            MemoryShare.revoked_at.is_(None), MemoryShare.expires_at > datetime.now(timezone.utc)).order_by(MemoryShare.created_at.desc()).all()]}


@router.delete('/api/v1/account/shares/{share_id}', status_code=204)
def revoke(share_id: UUID, db: Session = Depends(get_db), user=Depends(registered_user)):
    share = db.query(MemoryShare).filter(MemoryShare.id == share_id, MemoryShare.user_id == user.id).first()
    if share is None:
        raise HTTPException(404, 'Share link not found')
    share.revoked_at = datetime.now(timezone.utc)
    db.commit()


@router.post('/api/v1/shares/{token}/redeem', response_model=ItemDetail)
def redeem(token: str, db: Session = Depends(get_db), user=Depends(registered_user)):
    lock_account(db, user)
    share = active(db, token)
    now = datetime.now(timezone.utc)
    previous = db.get(ShareRedemption, (user.id, share.token_hash))
    if previous and previous.item_id:
        item = db.get(Item, previous.item_id)
        if item and item.deleted_at is None:
            return item
    snapshot = share.snapshot
    item = db.query(Item).filter(Item.user_id == user.id, Item.canonical_url == snapshot['url']).first()
    if item and item.deleted_at is None:
        return item
    if db.query(ShareRedemption).filter(ShareRedemption.user_id == user.id, ShareRedemption.created_at > now - timedelta(days=1)).count() >= 100:
        raise HTTPException(429, 'Too many shared memories saved today', headers={'Retry-After': '86400'})
    points = snapshot['key_points']
    new_item = item is None
    if new_item:
        item = Item(user_id=user.id, url=snapshot['url'], canonical_url=snapshot['url'])
    if item.id:
        db.query(Chunk).filter(Chunk.item_id == item.id).delete()
    item.deleted_at = None
    item.reprocess_snapshot = item.reprocess_failure = None
    item.category = item.intent = item.thumbnail_url = None
    item.title = item.title_clean = snapshot['title']
    item.summary = snapshot['summary']
    item.key_points = [p['point'] for p in points]
    item.brief_v2 = {'title': snapshot['title'], 'instant_brief': snapshot['summary'], 'key_points': points, 'content_type': snapshot['content_type'], 'brief_source': 'shared_snapshot'}
    item.source_domain = source_domain(snapshot['url'])
    item.source_type = source_type(snapshot['url'])
    item.shared_by = snapshot['shared_by']
    item.status = 'ready'
    item.processed_at = now
    item.content_id = None
    item.tags = []
    item.entities = {}
    item.fetch_metadata = item.evidence_bundle = item.processing_metadata = {}
    item.edited_title = item.edited_summary = item.failure_reason = None
    item.raw_text = item.normalized_text = item.raw_s3_key = item.raw_preview = None
    item.embedding = item.embedding_model = item.chunk_texts = item.chunk_timestamps = None
    item.link_only = item.needs_retry = False
    # shortcut: snapshots use lexical search; add sanitized embeddings when semantic-only retrieval is required.
    item.search_text = '\n'.join(filter(None, [snapshot['title'], snapshot['summary'], *item.key_points]))
    if new_item:
        try:
            with db.begin_nested():
                db.add(item)
                db.flush()
        except IntegrityError:
            winner = db.query(Item).filter(Item.user_id == user.id, Item.canonical_url == snapshot['url']).first()
            if winner is None:
                raise
            if winner.deleted_at is not None:
                raise HTTPException(409, 'This memory changed while saving. Try again') from None
            db.commit()
            return winner
    else:
        db.flush()
    if previous:
        previous.item_id = item.id
    else:
        db.add(ShareRedemption(user_id=user.id, token_hash=share.token_hash, item_id=item.id, created_at=now))
    db.commit()
    return item


@router.get('/s/{token}', response_class=HTMLResponse)
def landing(token: str, db: Session = Depends(get_db)):
    active(db, token)
    store = os.getenv('ANDROID_PLAY_STORE_URL', '')
    parsed = urlsplit(store)
    store_link = ('<p><a href="' + escape(store, quote=True) + '">Get FindBack on Google Play</a></p>') if parsed.scheme == 'https' and parsed.hostname == 'play.google.com' else ''
    host = urlsplit(os.getenv('SHARE_LINK_ORIGIN', 'https://findback.duckdns.org')).netloc
    package = os.getenv('ANDROID_APP_LINK_PACKAGE', 'com.findback.findback')
    app_link = escape('intent://' + host + '/s/' + token + '#Intent;scheme=https;package=' + package + ';end', quote=True)
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A memory shared with you · FindBack</title></head>
<body><main><h1>A memory shared with you</h1>
<p>Open this link in FindBack to save your own copy. Sign in to continue.</p>
<p><a href="{app_link}">Open in FindBack</a></p>
{store_link}
</main></body></html>""", headers={
        'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
        'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'"})


@router.get('/.well-known/assetlinks.json')
def assetlinks():
    fingerprints = [v.strip() for v in os.getenv('ANDROID_APP_LINK_FINGERPRINTS', '').split(',') if re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}', v.strip())]
    return [{'relation': ['delegate_permission/common.handle_all_urls'], 'target': {'namespace': 'android_app', 'package_name': os.getenv('ANDROID_APP_LINK_PACKAGE', 'com.findback.findback'), 'sha256_cert_fingerprints': fingerprints}}] if fingerprints else []
