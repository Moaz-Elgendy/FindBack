"""Why one user saved one piece of content (Phase 13).

Two kinds of knowledge about a memory, kept deliberately apart:

    content understanding   what the content contains -- the Brief, produced by
                           the pipeline, stored on the shared ContentAsset
    user context           why THIS user saved it -- a note and an intent,
                           stored on their own UserMemory row

They are separate because they have different owners and different lifetimes.
The content is deduplicated and can be shared; the reason for saving is nobody
else's business, and it changes without the content changing at all.

Nothing here writes to ContentAsset, and nothing here reads another user's row.
"""
from __future__ import annotations

import datetime
import logging

from sqlalchemy import exists
from sqlalchemy.orm import Session

from app.schemas import INTENT_PHRASES, UserIntent

log = logging.getLogger("findback.user_context")

# A note is a sentence, not a document.
MAX_NOTE_CHARS = 2000


def infer_intent(text: str) -> UserIntent | None:
    """Guess why this was saved, from the user's own words.

    Constrained to the enum: a value outside it is not a guess, it is a bug, so
    nothing can be inferred that the schema does not already define. Returns
    None when the wording is not clear -- "not stated" is a real answer, and a
    wrong guess about someone's reason for saving is worse than no guess.
    """
    lowered = (text or "").lower()
    if not lowered.strip():
        return None
    for value, phrases in INTENT_PHRASES.items():
        if any(phrase in lowered for phrase in phrases):
            return UserIntent(value)
    return None


def get_memory(db: Session, user_id, content_id) -> "object | None":
    """This user's memory of this content, and only theirs."""
    from app.models import Item, UserMemory

    return (db.query(UserMemory)
            .filter(UserMemory.user_id == user_id,
                    UserMemory.content_id == content_id,
                    ~exists().where(Item.user_id == user_id, Item.content_id == content_id,
                                    Item.deleted_at.is_not(None)))
            .first())


def set_context(db: Session, user_id, content_id, note=None,
                intent=None, commit: bool = True):
    """Set or update the note and/or the intent on this user's memory.

    Only the fields the caller actually supplied are written: patching only the
    intent must not blank an existing note, which is the one piece of the row
    that cannot be recovered.

    `intent` is validated against the schema before it reaches the database, so
    an unrecognised reason is a 422 rather than a poisoned column.
    """
    memory = get_memory(db, user_id, content_id)
    if memory is None:
        return None

    if note is not None:
        cleaned = note.strip()
        if len(cleaned) > MAX_NOTE_CHARS:
            raise ValueError(f"note is too long (max {MAX_NOTE_CHARS} characters)")
        memory.user_note = cleaned or None
    if intent is not None:
        # A value outside the enum is rejected here rather than stored.
        memory.user_intent = UserIntent(intent).value

    memory.updated_at = datetime.datetime.now(datetime.timezone.utc)
    if commit:
        db.commit()
        db.refresh(memory)
    return memory


def clear_context(db: Session, user_id, content_id, commit: bool = True):
    """Forget the note and the intent, keeping the save itself.

    Removing a note is a real need: a user should be able to withdraw what they
    said. It must not touch the content, and it must not delete the memory.
    """
    memory = get_memory(db, user_id, content_id)
    if memory is None:
        return None
    memory.user_note = None
    memory.user_intent = None
    memory.updated_at = datetime.datetime.now(datetime.timezone.utc)
    if commit:
        db.commit()
        db.refresh(memory)
    return memory
