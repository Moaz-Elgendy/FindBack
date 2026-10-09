"""Where this user's devices are, so a notification can be delivered later.

Storage only: nothing is sent from here. The transport that reads these rows is
a later phase.

A push registration belongs to the DEVICE, not to the person, so `token` is
unique across all accounts and registering from a second account MOVES the row
instead of leaving the first account holding a token for a phone that is no
longer signed in to it. Without that, a shared family device would keep
notifying whoever signed in to it first.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import DeviceToken

router = APIRouter(prefix='/api/v1/devices', tags=['devices'])

# FCM registration tokens are long base64-ish strings and APNs tokens are 64 hex
# characters. The cap is generous but finite, so an oversized body cannot end up
# stored whole.
MAX_TOKEN_LENGTH = 4096


class DeviceRegistration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(min_length=1, max_length=MAX_TOKEN_LENGTH)
    platform: str = Field(pattern='^(android|ios)$')

    @field_validator('token')
    @classmethod
    def trim(cls, value):
        # The token arrives from the platform SDK, so surrounding whitespace is
        # a client bug rather than something worth storing.
        value = value.strip()
        if not value:
            raise ValueError('token cannot be blank')
        return value


@router.post('', status_code=201)
def register_device(body: DeviceRegistration, db: Session = Depends(get_db),
                    user=Depends(get_current_user)):
    """Register this device for the signed-in user, or move it to them.

    Upsert on the unique token, so re-registering on every launch neither
    creates a duplicate nor fails. `user_id` is part of the update set: a device
    that changes hands moves to the new account in the same statement, which is
    also what stops the previous account being notified about it any more.

    `created_at` is deliberately not in the update set, so the row still says
    when this device was first enrolled rather than when it last checked in.
    """
    values = {'user_id': user.id, 'token': body.token,
              'platform': body.platform,
              # Bumped on every registration, which is how a later phase can
              # tell a token the app has just confirmed from a stale one.
              'updated_at': func.now()}
    db.execute(insert(DeviceToken).values(**values)
               .on_conflict_do_update(index_elements=['token'], set_=values))
    db.commit()
    return {'registered': True, 'platform': body.platform}


@router.delete('/{token}', status_code=204)
def remove_device(token: str, db: Session = Depends(get_db),
                  user=Depends(get_current_user)):
    """Remove this device from the signed-in user's registrations.

    Scoped by `user_id` as well as by token, and 404 rather than 204 when the
    token belongs to somebody else: a caller must not be able to learn that a
    token they do not hold is registered at all, and must never delete another
    account's registration.
    """
    removed = db.query(DeviceToken).filter(
        DeviceToken.token == token, DeviceToken.user_id == user.id).delete()
    if not removed:
        raise HTTPException(404, 'not found')
    db.commit()
    return None