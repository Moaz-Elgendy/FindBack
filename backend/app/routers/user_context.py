"""Why the user saved something (Phase 13).

Every route here reads and writes the caller's own `UserMemory` row and nothing
else. The content it points at is shared, the reason for saving is not, so no
route in this file writes to ContentAsset.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import ContentAsset
from app.schemas import UserContextIn, UserContextResponse
from app.services import user_context

router = APIRouter(prefix="/api/v1/memories", tags=["user-context"])


def _response(db: Session, user, memory) -> UserContextResponse:
    asset = (db.query(ContentAsset)
             .filter(ContentAsset.id == memory.content_id).first())
    return UserContextResponse(
        id=memory.id, content_id=memory.content_id, note=memory.user_note,
        intent=memory.user_intent, save_count=memory.save_count,
        first_saved_at=memory.first_saved_at, last_saved_at=memory.last_saved_at,
        content_title=asset.title if asset else None,
    )


@router.get("/{content_id}", response_model=UserContextResponse)
def get_context(content_id: UUID, db: Session = Depends(get_db),
                user=Depends(get_current_user)):
    """This user's note and intent for one piece of content."""
    memory = user_context.get_memory(db, user.id, content_id)
    if memory is None:
        # 404 rather than an empty object: "you have not saved this" and "you
        # saved this with no note" must not look the same.
        raise HTTPException(404, "no memory of this content")
    return _response(db, user, memory)


@router.patch("/{content_id}", response_model=UserContextResponse)
def update_context(content_id: UUID, payload: UserContextIn,
                   db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Set or update the note and/or the intent.

    Only supplied fields are written, so patching the intent does not wipe a
    note the user already wrote.
    """
    memory = user_context.get_memory(db, user.id, content_id)
    if memory is None:
        raise HTTPException(404, "no memory of this content")
    if payload.note is None and payload.intent is None:
        raise HTTPException(422, "supply a note or an intent")
    try:
        user_context.set_context(db, user.id, content_id,
                                 note=payload.note,
                                 intent=payload.intent.value if payload.intent else None)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return _response(db, user, memory)


@router.delete("/{content_id}", response_model=UserContextResponse)
def clear_context(content_id: UUID, db: Session = Depends(get_db),
                  user=Depends(get_current_user)):
    """Withdraw the note and the intent. The save itself is kept."""
    memory = user_context.clear_context(db, user.id, content_id)
    if memory is None:
        raise HTTPException(404, "no memory of this content")
    return _response(db, user, memory)
