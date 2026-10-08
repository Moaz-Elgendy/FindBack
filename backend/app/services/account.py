"""Delete only the caller's data; keep assets shared by other accounts."""
import hashlib
import os
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from app.models import DeletedIdentity, User
from app.services.storage import delete_raw_snapshots


def subject_hash(subject):
    return hashlib.sha256(subject.encode('utf-8')).hexdigest()


def delete_provider_identity(user):
    base = os.getenv('SUPABASE_URL', '').rstrip('/')
    key = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')
    parsed = urlsplit(base)
    try:
        subject = str(UUID(user.auth_subject or ''))
    except ValueError:
        raise HTTPException(503, 'Account deletion is not configured for this identity') from None
    if (not key or parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.query or parsed.fragment or user.auth_provider != base + '/auth/v1'):
        raise HTTPException(503, 'Account deletion is not configured for this identity')
    try:
        response = httpx.delete(base + '/auth/v1/admin/users/' + subject,
            headers={'apikey': key, 'Authorization': 'Bearer ' + key}, timeout=15, follow_redirects=False)
    except httpx.HTTPError:
        raise HTTPException(502, 'Could not delete your account. Please try again') from None
    # A retry after provider deletion may find the identity already removed.
    if response.status_code not in (200, 204, 404):
        raise HTTPException(502, 'Could not delete your account. Please try again')


def delete_account(db, user):
    owner = db.query(User).filter(User.id == user.id).with_for_update().first()
    if owner is None:
        return
    item_ids = [str(row[0]) for row in db.execute(text('SELECT id FROM items WHERE user_id=:u'), {'u': owner.id})]
    delete_raw_snapshots(item_ids)
    delete_provider_identity(owner)
    db.execute(insert(DeletedIdentity).values(subject_hash=subject_hash(owner.auth_subject)).on_conflict_do_nothing())
    # PUBLIC assets are shared; legacy rows may still have an owner attached.
    db.execute(text("UPDATE content_assets SET owner_user_id=NULL WHERE owner_user_id=:u AND visibility='PUBLIC'"), {'u': owner.id})
    # The composite item->memory FK requires items to be removed first.
    db.execute(text('DELETE FROM items WHERE user_id=:u'), {'u': owner.id})
    db.execute(text('DELETE FROM user_memories WHERE user_id=:u'), {'u': owner.id})
    db.delete(owner)
    db.commit()
