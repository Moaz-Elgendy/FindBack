"""Freeze one user's forgotten saves into a weekly-note snapshot.

The note says "3 things you saved and forgot"; the "Worth another look" screen
has to show exactly those three. Recomputing the set when the user taps would
let it drift away from the number on the lock screen (a save opened in between
would drop out), so the ids are stored at send time instead.

The definition of "forgotten" is NOT repeated here -- it lives in
`services/forgotten.py` and is imported, so the note can never count something
different from what `forgotten_save_ids` counts.
"""
from __future__ import annotations

import datetime
import uuid

from sqlalchemy.orm import Session

from app.models import SnapshotItem, WeeklySnapshot
from app.services.forgotten import forgotten_save_ids


def create_snapshot(db: Session, user_id, now: datetime.datetime | None = None,
                    iso_week: str | None = None):
    """Store this user's forgotten save ids and return the snapshot.

    Returns the new `WeeklySnapshot`, or None when the user has nothing
    forgotten -- the addendum (section 1.2) says a zero count sends nothing, so
    an empty snapshot would be a note with no list behind it. Nothing is
    written in that case, not even an empty row.

    `iso_week` stamps the week's unique key on the row as part of the SAME
    insert. The weekly task passes it so a duplicate run fails at the database
    and is rolled back whole. Claiming the key in a second statement after the
    commit would leave an unkeyed row behind every time it lost the race.

    Raises IntegrityError when that week is already claimed for this user.

    `now` is passed through to `forgotten_save_ids` for the same reason it
    exists there: the seven-day boundary has to be testable exactly.
    """
    save_ids = forgotten_save_ids(db, user_id, now=now)
    if not save_ids:
        return None

    snapshot = WeeklySnapshot(id=uuid.uuid4(), user_id=user_id, iso_week=iso_week)
    db.add(snapshot)
    # Flush the parent before the children: nothing in the ORM declares that a
    # snapshot_item depends on a weekly_snapshot, so without this the unit of
    # work would order the two INSERTs the other way round and the child's
    # foreign key would fail. The commit still covers both, so a lost race on
    # the unique key rolls the whole snapshot back.
    db.flush()
    db.add_all(SnapshotItem(snapshot_id=snapshot.id, save_id=uuid.UUID(save_id))
               for save_id in save_ids)
    db.commit()
    return snapshot