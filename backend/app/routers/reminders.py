"""Private reminders, scheduled natively on the user's device."""
from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

from app.auth import get_current_user
from app.database import get_db
from app.models import Item, Reminder

router = APIRouter(prefix='/api/v1', tags=['reminders'])


class ReminderTime(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scheduled_at: AwareDatetime


class ReminderSet(ReminderTime):
    time_zone: str

    @field_validator('time_zone')
    @classmethod
    def valid_zone(cls, value):
        if len(value) > 100:
            raise ValueError('Use an IANA time zone')
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Use an IANA time zone') from None
        return value

    @field_validator('scheduled_at')
    @classmethod
    def future_time(cls, value):
        if value <= datetime.now(timezone.utc):
            raise ValueError('Choose a future time')
        return value.astimezone(timezone.utc)


class ReminderResponse(ReminderSet):
    model_config = ConfigDict(from_attributes=True)
    item_id: UUID
    delivered_at: AwareDatetime | None = None

    # Responses include overdue reminders: never validate them as new choices.
    @field_validator('scheduled_at')
    @classmethod
    def future_time(cls, value):
        return value.astimezone(timezone.utc)


def owned_item(db, user, item_id):
    item = db.query(Item).filter(Item.id == item_id, Item.user_id == user.id,
                                  Item.deleted_at.is_(None)).with_for_update().first()
    if item is None:
        raise HTTPException(404, 'not found')
    return item


@router.get('/reminders')
def list_reminders(db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = (db.query(Reminder).join(Item, Item.id == Reminder.item_id)
            .filter(Item.user_id == user.id, Item.deleted_at.is_(None), Reminder.delivered_at.is_(None))
            .order_by(Reminder.scheduled_at, Reminder.item_id).all())
    return {'reminders': [ReminderResponse.model_validate(row).model_dump() for row in rows]}


@router.get('/items/{item_id}/reminder', response_model=ReminderResponse | None)
def get_reminder(item_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    owned_item(db, user, item_id)
    return db.get(Reminder, item_id)


@router.put('/items/{item_id}/reminder', response_model=ReminderResponse)
def set_reminder(item_id: UUID, value: ReminderSet, db: Session = Depends(get_db), user=Depends(get_current_user)):
    owned_item(db, user, item_id)
    values = value.model_dump() | {'delivered_at': None}
    db.execute(insert(Reminder).values(item_id=item_id, **values)
               .on_conflict_do_update(index_elements=['item_id'], set_=values))
    db.commit()
    return db.get(Reminder, item_id)


@router.delete('/items/{item_id}/reminder', status_code=204)
def remove_reminder(item_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    owned_item(db, user, item_id)
    db.query(Reminder).filter(Reminder.item_id == item_id).delete()
    db.commit()


@router.post('/items/{item_id}/reminder/delivered', status_code=204)
def delivered(item_id: UUID, value: ReminderTime, db: Session = Depends(get_db), user=Depends(get_current_user)):
    owned_item(db, user, item_id)
    # A callback from the previous alarm must not consume a replacement.
    db.query(Reminder).filter(Reminder.item_id == item_id, Reminder.scheduled_at == value.scheduled_at,
                             Reminder.scheduled_at <= datetime.now(timezone.utc)).update(
        {'delivered_at': datetime.now(timezone.utc)})
    db.commit()
