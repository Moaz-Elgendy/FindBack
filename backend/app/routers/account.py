from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session, defer

from app.auth import get_current_user
from app.database import get_db
from app.models import Item, UserMemory, WeeklyNotePreference
from app.schemas import ItemDetail
from app.services.account import delete_account

router = APIRouter(prefix='/api/v1/account', tags=['account'])


class WeeklyNoteSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', from_attributes=True)
    enabled: bool = False
    weekday: int = Field(6, ge=0, le=6)
    hour: int = Field(18, ge=0, le=23)
    minute: int = Field(0, ge=0, le=59)
    time_zone: str = Field('UTC', max_length=100)

    @field_validator('time_zone')
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Use an IANA time zone') from None
        return value


@router.get('/weekly-note', response_model=WeeklyNoteSettings)
def get_weekly_note(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return db.get(WeeklyNotePreference, user.id) or WeeklyNoteSettings()


@router.put('/weekly-note', response_model=WeeklyNoteSettings)
def set_weekly_note(value: WeeklyNoteSettings, db: Session = Depends(get_db), user=Depends(get_current_user)):
    from sqlalchemy.dialects.postgresql import insert
    data = value.model_dump()
    db.execute(insert(WeeklyNotePreference).values(user_id=user.id, **data)
        .on_conflict_do_update(index_elements=['user_id'], set_=data))
    db.commit()
    return value


@router.get('/export')
def export_saves(db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = (db.query(Item, UserMemory).options(defer(Item.embedding), defer(Item.raw_text),
        defer(Item.normalized_text), defer(Item.chunk_texts), defer(Item.chunk_timestamps), defer(Item.search_text))
        .outerjoin(UserMemory,
        (UserMemory.user_id == Item.user_id) & (UserMemory.content_id == Item.content_id))
        .filter(Item.user_id == user.id, Item.deleted_at.is_(None))
        .order_by(Item.created_at.desc(), Item.id.desc()).all())
    saves = []
    for item, memory in rows:
        detail = ItemDetail.model_validate(item)
        saves.append({'id': str(item.id), 'url': item.url, 'title': detail.title,
            'summary': detail.summary, 'saved_at': item.created_at,
            'type': detail.content_type, 'tags': detail.tags,
            'brief': detail.model_dump(include={'instant_brief', 'best_takeaway', 'key_points_with_refs', 'key_points', 'entities', 'topics'}),
            'note': memory.user_note if memory else None,
            'intent': memory.user_intent if memory else None})
    return {'version': 1, 'exported_at': datetime.now(timezone.utc), 'saves': saves}


@router.delete('', status_code=204)
def remove_account(db: Session = Depends(get_db), user=Depends(get_current_user)):
    delete_account(db, user)
    return Response(status_code=204)
