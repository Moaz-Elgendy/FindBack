"""What counts as a "forgotten" save, in one place.

The weekly note promises to remind a user of saves they never got around to
(docs/PHASES.md, sections 1.2/1.3), so the
definition lives here rather than inside a route or a future task:

    created more than 7 days ago, never opened, not deleted, one user.

"Never opened" is `first_opened_at IS NULL`. Saves older than migration 0020
have NULL too, and are treated as forgotten on purpose -- there is nothing to
backfill, because as far as the data goes they were never opened.
"""
from __future__ import annotations

import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

# The threshold, named so the test and any future caller read the same value.
FORGOTTEN_AFTER_DAYS = 7


def forgotten_save_ids(db: Session, user_id,
                       now: datetime.datetime | None = None) -> list[str]:
    """This one user's forgotten save ids, newest save first.

    Scoped to `user_id` inside the query itself, so no other user's rows can
    reach the result. Returns an empty list when there is nothing to show.
    `now` exists so the seven-day boundary can be tested exactly.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=FORGOTTEN_AFTER_DAYS)
    rows = db.execute(text(
        "SELECT id::text FROM items "
        "WHERE user_id = :u AND created_at < :cutoff "
        "AND first_opened_at IS NULL AND deleted_at IS NULL "
        "ORDER BY created_at DESC, id DESC"),
        {"u": str(user_id), "cutoff": cutoff}).all()
    return [row[0] for row in rows]
