"""Build the weekly note for every user whose chosen moment has arrived.

The task is a fifteen-minute tick, and the user's choice is a day, an hour and
a minute in THEIR saved IANA zone. Those two do not line up, so this module
owns the matching rule and the Celery task only calls `run_weekly_note`.

Why zoneinfo and not a stored UTC offset
----------------------------------------
A stored offset would be wrong twice a year. `zoneinfo` resolves the zone at
the instant being tested, so "Sunday 09:00 in New York" is 13:00 UTC in summer
and 14:00 UTC in winter: the user keeps their chosen wall-clock moment across a
DST change instead of being notified an hour off twice a year.

Why a unique key and not a "did I already send?" query
-------------------------------------------------------
Check-then-send is a race: two beat instances can both read "not sent yet" and
both send. The `weekly_snapshots_user_week_uq` key makes the second insert fail
at the database, so a repeated run cannot notify twice however many workers are
running.

Delivery uses FCM HTTP v1, with generic lock-screen text and scoped snapshot reads.
"""
from __future__ import annotations

import datetime
import logging
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import env
from app.database import SessionLocal
from app.models import DeviceToken, SnapshotItem, WeeklyNotePreference, WeeklySnapshot
from app.services.snapshots import create_snapshot

log = logging.getLogger("findback.weekly_note")

# The lock-screen body. Fixed, and identical for every user and every count: it
# is the one string in the notification that can never leak what was saved.
WEEKLY_NOTE_BODY = 'Open FindBack to take another look.'

# FCM HTTP v1. The scope is the narrowest one that only sends messages.
FCM_SEND_URL = ('https://fcm.googleapis.com/v1/projects/'
                '{project}/messages:send')
FCM_SCOPE = ['https://www.googleapis.com/auth/firebase.messaging']

# Five seconds for the whole request, and no more than ten devices per user.
#
# Sends are sequential, so the per-user cost is N x this timeout, and the task
# runs under a soft time limit it must never reach: hitting it kills the worker
# mid-tick and strands the claim it was holding. A user with twenty devices
# could previously spend 200 seconds of the budget on one account.
#
# The newest ten is the right cut: the most recently registered devices are the
# ones actually in use, and the oldest are the ones most likely to have been
# uninstalled without ever telling us.
FCM_TIMEOUT = httpx.Timeout(5.0)
MAX_DEVICES_PER_USER = 10

# Test seams only, mirroring app/services/ai.py: unit tests install an
# httpx.MockTransport here so no test ever reaches the network. Production
# leaves both at these values (transport=None means httpx's default).
_TRANSPORT: httpx.BaseTransport | None = None
_CREDENTIALS: object | None = None

# FCM error details that mean "this token will never work again". Anything else
# -- a quota error, a 5xx, an auth failure -- is transient and must not delete
# a working registration.
DEAD_TOKEN_REASONS = frozenset({'UNREGISTERED', 'INVALID_ARGUMENT'})

# Age at which an undelivered claim is treated as stranded, and the sweep that
# deletes it.
#
# A claim this old with no `delivered_at` was left by a worker that died
# without running any handler -- SIGKILL, an OOM kill, the host going away --
# and no code path will ever revisit it.
#
# WHAT THE SWEEP IS FOR: bounding the table. Without it, a deployment that keeps
# crashing accumulates one abandoned claim per user per week, forever, since
# nothing else would ever remove them. That is the whole benefit.
#
# WHAT THE SWEEP IS NOT FOR: recovering the missed note. The threshold is
# deliberately longer than the widest due window, because anything shorter
# would free a claim another worker is still legitimately working on. The
# consequence is that by the time a sweep fires the user's due window has
# already closed, so `due_now` is false and that week's note is NOT re-delivered
# -- it is simply missed. The next week's tick serves the user again.
STALE_CLAIM_AFTER = datetime.timedelta(minutes=30)

# Fraction of the task's soft time limit this tick will spend before it starts
# no more users. The remaining 20% covers the release paths, the final commit
# and the network round trip to FCM, so the worker is not killed while holding
# an open transaction.
TASK_BUDGET_FRACTION = 0.8


def _soft_time_limit() -> int:
    """The task's soft limit in seconds, from the same env Celery reads."""
    return env.get_int('TASK_SOFT_TIME_LIMIT', 900)


def release_stale_claims(db: Session,
                         now: datetime.datetime) -> int:
    """Give back claims nobody ever delivered. Returns how many were freed.

    Runs before the loop, so a user whose week is stranded is not simply told
    `already_sent` and skipped for a second week.
    """
    cutoff = now - STALE_CLAIM_AFTER
    stale = db.query(WeeklySnapshot).filter(
        WeeklySnapshot.delivered_at.is_(None),
        WeeklySnapshot.created_at < cutoff).all()
    for snapshot in stale:
        db.delete(snapshot)
    if stale:
        db.commit()
        log.warning("weekly note: released %d stale week claim(s) with no "
                    "delivered note", len(stale))
    return len(stale)


def _access_token(credentials_file: str) -> str | None:
    """A short-lived OAuth token for the service account, or None.

    The credential object is cached on the module: loading a service-account
    key parses and validates a private key on every call, and this runs once
    per device per note. `google.auth` refreshes the token itself and only hits
    the network when the cached one has expired.
    """
    global _CREDENTIALS
    try:
        if _CREDENTIALS is None:
            from google.oauth2 import service_account
            _CREDENTIALS = service_account.Credentials.from_service_account_file(
                credentials_file, scopes=FCM_SCOPE)
        if not _CREDENTIALS.valid:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account
            _CREDENTIALS.refresh(Request())
        return _CREDENTIALS.token
    except Exception as exc:  # noqa: BLE001 - a bad key must not kill the tick
        # Type and length only: a google-auth exception can quote the key file
        # contents, and this log is shipped and grepped.
        log.error("weekly note: FCM credentials unusable: %s",
                  _safe_error(exc))
        return None


def _safe_error(exc: BaseException) -> str:
    """Type and length only -- see `observability.describe_exc`."""
    from app.services.observability import describe_exc
    return describe_exc(exc)


def _post_message(client: httpx.Client, access_token: str, project: str,
                  token: str, platform: str, title: str, snapshot_id: str,
                  account_id: str) -> httpx.Response:
    """Send one note to one device.

    `notification` holds the lock-screen text; `data` holds ONLY the tap target.
    FCM requires the two to agree, so both are set, and the data payload carries
    no title for the same reason the notification does not: it is not private.

    `android.notification.visibility=private` is what makes the lock screen show
    "FindBack" instead of the body's text when the device is locked.
    """
    message = {
        'token': token,
        'notification': {
            'title': title,
            'body': WEEKLY_NOTE_BODY,
        },
        # Data payloads are strings only, and nothing else may be added.
        #
        # `account_id` is the account this note belongs to. The app compares it
        # with the signed-in account and refuses to open a note addressed to
        # somebody else, which is the client-side half of the guard described
        # in migration 0023: a device token that changed hands must not become
        # a way to walk into another account's saved list.
        'data': {'type': 'weekly_note', 'snapshot_id': snapshot_id,
                 'account_id': account_id},
    }
    if platform == 'android':
        message['android'] = {'notification': {
            'visibility': 'PRIVATE', 'tag': f'weekly-note-{snapshot_id}'}}
    return client.post(FCM_SEND_URL.format(project=project),
                       json={'message': message},
                       headers={'Authorization': f'Bearer {access_token}'})


def _project_id() -> str | None:
    """The Firebase project to send to, or None when it cannot be determined.

    `FCM_PROJECT_ID` wins, because it is the explicit override. Otherwise the
    project is read out of the service account's own client address:
    `firebase-adminsdk-xxxxx@<project>.iam.gserviceaccount.com`.

    There is deliberately NO default. A hard-coded project name would send
    real notifications into somebody else's Firebase project, or fail with a
    404 that looks like a credential problem, and neither is discoverable from
    a log line. An unresolvable project is a configuration error and is
    reported as one.
    """
    override = env.get('FCM_PROJECT_ID')
    if override:
        return override
    client_email = getattr(_CREDENTIALS, 'service_account_email', '') or ''
    if '@' in client_email:
        project = client_email.split('@', 1)[1].split('.', 1)[0]
        if project:
            return project
    return None


def _failure_reason(response: httpx.Response) -> str | None:
    """The FCM error status, e.g. 'UNREGISTERED', or None if not recognisable."""
    try:
        error = response.json().get('error', {})
        for detail in error.get('details', []):
            if detail.get('@type') == 'type.googleapis.com/google.firebase.fcm.v1.FcmError':
                return detail.get('errorCode')
        # A generic INVALID_ARGUMENT can mean a bad message, not a bad token.
        status = error.get('status')
        return status if status != 'INVALID_ARGUMENT' else None
    except (ValueError, AttributeError, TypeError):
        return None


def db_session_device_tokens(user_id) -> list[tuple[str, str]]:
    """This user's registered devices as (token, platform), newest first.

    Capped at [MAX_DEVICES_PER_USER]: the sends are sequential and each costs up
    to `FCM_TIMEOUT`, so an unbounded list is a way for one account to consume
    the task's whole time budget. Ordered by `updated_at` so the cut keeps the
    devices that have been in use most recently.
    """
    session = SessionLocal()
    try:
        return [(row[0], row[1]) for row in session.query(
            DeviceToken.token, DeviceToken.platform)
            .filter(DeviceToken.user_id == user_id)
            .order_by(DeviceToken.updated_at.desc(), DeviceToken.id.desc())
            .limit(MAX_DEVICES_PER_USER).all()]
    finally:
        session.close()


def _forget_token(token: str) -> None:
    """Drop a token FCM has reported as permanently dead."""
    session = SessionLocal()
    try:
        session.query(DeviceToken).filter(DeviceToken.token == token).delete()
        session.commit()
    except Exception as exc:  # noqa: BLE001 - a failed cleanup must not stop the rest
        session.rollback()
        log.warning("weekly note: could not remove a dead device token: %s",
                    _safe_error(exc))
    finally:
        session.close()

# How often the task runs. The due window is this wide too, because the user's
# chosen minute need not land on a tick: someone who picked 09:07 is served by
# the 09:15 tick.
TICK_MINUTES = 15


def local_time(preference: WeeklyNotePreference,
               now: datetime.datetime) -> datetime.datetime | None:
    """`now` in the user's saved zone, or None if the zone is unusable."""
    try:
        return now.astimezone(ZoneInfo(preference.time_zone))
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        # A zone this host does not know. Skipping is the only safe answer:
        # guessing one could send at the wrong hour on the wrong day.
        log.warning("weekly note: unusable time zone %r, skipping",
                    preference.time_zone)
        return None


def due_now(preference: WeeklyNotePreference, now: datetime.datetime) -> bool:
    """Is `now` inside this user's chosen moment, in their own zone?

    `now` is UTC and is converted before anything is compared, so weekday, hour
    and minute are the user's local wall clock on that date -- DST included.
    """
    local = local_time(preference, now)
    if local is None:
        return False
    scheduled = (local - datetime.timedelta(
        days=(local.weekday() - preference.weekday) % 7)).replace(
            hour=preference.hour, minute=preference.minute, second=0, microsecond=0)
    return 0 <= (local - scheduled).total_seconds() < TICK_MINUTES * 60


def iso_week(local: datetime.datetime) -> str:
    """The ISO week of a local wall clock, as 'YYYY-Www'.

    Computed from local time, not UTC, so Monday morning in Auckland is still
    this week while UTC is still last week.
    """
    year, week, _ = local.isocalendar()
    return f"{year}-W{week:02d}"


class PushResult:
    """What one delivery attempt actually achieved.

    Returned rather than None so the caller can tell three outcomes apart,
    which it must because they mean different things for the week's claim:

      * `delivered` -- at least one device took the message. The claim stays.
      * `attempted` but nothing delivered -- a transient FCM failure. The claim
        is released so a later tick inside the same window can retry.
      * not `attempted` -- FCM is unconfigured, the key is unusable, or the user
        has no device. Nothing was tried, so there is nothing to retry *yet* and
        the claim is released so the note is not lost while the problem is fixed.
    """

    __slots__ = ('attempted', 'sent', 'failed', 'removed', 'reason')

    def __init__(self, attempted: bool, sent: int = 0, failed: int = 0,
                 removed: int = 0, reason: str = ''):
        self.attempted = attempted
        self.sent = sent
        self.failed = failed
        self.removed = removed
        self.reason = reason

    @property
    def delivered(self) -> bool:
        """True when at least one device accepted the note."""
        return self.sent > 0

    def __eq__(self, other):
        return isinstance(other, PushResult) and (
            self.attempted, self.sent, self.failed, self.removed,
            self.reason) == (other.attempted, other.sent, other.failed,
                             other.removed, other.reason)

    def __repr__(self):
        return (f'PushResult(attempted={self.attempted}, sent={self.sent}, '
                f'failed={self.failed}, removed={self.removed}, '
                f'reason={self.reason!r})')


def send_weekly_push(user_id, snapshot_id, count: int) -> PushResult:
    """Deliver the note to every device this user has registered.

    The lock-screen text is deliberately generic and is built here from `count`
    alone -- no title, URL or summary is ever read from the snapshot. A
    notification is visible to anyone who picks up the phone, including over a
    shoulder and on a lock screen, so it says how many and nothing about what
    (redesign addendum, section 1.1).

    The payload carries `snapshot_id` and nothing else, because the app fetches
    the list itself after the tap. Sending the titles to save a round trip would
    put the user's private content in a payload that is logged, cached and
    visible in developer tools.

    Delivery is best-effort per device: one dead token must not stop the others,
    and a token FCM reports as dead is deleted rather than retried forever.

    The returned [PushResult] tells the caller whether to keep the week's
    claim. A `delivered` result means the note reached a device and the claim
    must stay, so the user is not notified twice; anything else means the claim
    is released and a later tick may retry.
    """
    credentials_file = env.get('FCM_SERVICE_ACCOUNT_FILE')
    if not credentials_file:
        # Not configured is a normal state for a developer machine and for any
        # deployment that has not enabled push yet, so it is a warning and not
        # an exception.
        log.warning("weekly note: FCM_SERVICE_ACCOUNT_FILE is not set; "
                    "skipping push delivery")
        return PushResult(attempted=False, reason='unconfigured')

    # Credentials are resolved before the device lookup: if the key is unusable
    # there is nothing to send, and a needless query per user per tick is
    # worth avoiding on a tick that walks every enabled account.
    access_token = _access_token(credentials_file)
    if access_token is None:
        return PushResult(attempted=False, reason='credentials_unusable')

    project = _project_id()
    if not project:
        # An unresolvable project is a misconfiguration, not a delivery
        # problem, so nothing is attempted and the caller releases the claim
        # and says why rather than posting into an unknown project.
        log.error("weekly note: cannot determine the Firebase project; set "
                  "FCM_PROJECT_ID or use a service account whose client_email "
                  "names the project")
        return PushResult(attempted=False, reason='project_unknown')

    try:
        devices = db_session_device_tokens(user_id)
    except Exception as exc:  # noqa: BLE001 - see _safe_error
        # Not a network problem, but this function's contract is that it always
        # answers with a PushResult. The caller's per-user guard would catch it
        # too; returning here keeps the two paths from behaving differently.
        log.error("weekly note: could not read devices for user %s: %s",
                  user_id, _safe_error(exc))
        return PushResult(attempted=False, reason='device_lookup_failed')

    if not devices:
        log.info("weekly note: user %s has no registered devices", user_id)
        return PushResult(attempted=False, reason='no_devices')

    title = f'{count} thing{"s" if count != 1 else ""} you saved and forgot'
    removed = 0
    sent = 0
    failed = 0
    with httpx.Client(timeout=FCM_TIMEOUT, transport=_TRANSPORT) as client:
        for token, platform in devices:
            # A transport failure is transient, exactly like a 5xx: FCM was
            # never asked. It must not escape, because this function promises
            # the caller a PushResult, and an exception here would skip the
            # claim release and abort the rest of the tick.
            try:
                response = _post_message(client, access_token, project, token,
                                          platform, title, str(snapshot_id),
                                          str(user_id))
            except httpx.HTTPError as exc:
                # ConnectError, TimeoutException and every other transport
                # error subclass HTTPError. Type and length only: the message
                # can contain the URL and the body, and the token is a
                # credential.
                failed += 1
                log.warning("weekly note: delivery attempt failed: %s",
                            _safe_error(exc))
                continue
            if response.status_code == 200:
                sent += 1
                continue
            reason = _failure_reason(response)
            if reason in DEAD_TOKEN_REASONS:
                # UNREGISTERED means the app was uninstalled; INVALID_ARGUMENT
                # means the token is malformed. Neither will ever deliver again,
                # so keeping the row only invites a retry on every future note.
                _forget_token(token)
                removed += 1
                log.info("weekly note: removed a dead device token (%s)", reason)
            else:
                # Transient (quota, 5xx, auth). Logged by status only: the body
                # can echo the token back, and a token is a live credential.
                failed += 1
                log.warning("weekly note: delivery failed with status %s",
                            response.status_code)
    return PushResult(attempted=True, sent=sent, failed=failed, removed=removed,
                      reason='delivered' if sent else 'not_delivered')


def _release_claim(db: Session, snapshot: WeeklySnapshot) -> None:
    """Give the week back so a later tick can retry.

    The unique key is what stops a double send, but it is only correct while
    the note actually went out. Keeping a claim for a note that was never
    delivered loses that user's note for the whole week over a configuration
    problem or one bad minute.

    Deleting the snapshot is what frees the key: `snapshot_items` cascades, and
    nothing else references it because no device ever received this id.
    """
    db.delete(snapshot)
    db.commit()


def _release_claim_quietly(db: Session, claimed: list[WeeklySnapshot]) -> None:
    """Release after an exception, and never raise from the cleanup itself.

    The session is already in an unknown state at this point -- the statement
    that failed may have left it unusable -- so it is rolled back first. A
    failure here is logged and swallowed: it is the last thing standing between
    an exception and the rest of the tick, and raising would lose the remaining
    users as well.
    """
    if not claimed:
        return
    try:
        db.rollback()
        for snapshot in claimed:
            db.delete(snapshot)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - cleanup must not propagate
        db.rollback()
        log.warning("weekly note: could not release a week claim: %s",
                    _safe_error(exc))


def _send_if_due(db: Session, preference: WeeklyNotePreference,
                 now: datetime.datetime,
                 claimed: list[WeeklySnapshot] | None = None) -> str:
    """Create this week's snapshot and hand it to the sender.

    Returns 'sent', 'already_sent', 'nothing_to_show' or 'retry'. The returned
    string is what the summary counts, so a duplicate run is distinguishable
    from a user who simply has nothing forgotten, and from one whose delivery
    did not happen.

    The claim is kept only when at least one device took the message. Every
    other outcome releases it, which is safe because `due_now` gates this
    whole path: once the user's window closes there is no further attempt for
    that week, so retries cannot run away.

    `claimed` is appended to the moment the week's key is taken, so the caller
    can give it back if anything raises after that point. Anything raised here
    is the caller's to catch: this function must not know about the tick.
    """
    local = local_time(preference, now)
    scheduled_day = local - datetime.timedelta(
        days=(local.weekday() - preference.weekday) % 7)
    # The week key goes in with the INSERT. Losing that race therefore rolls
    # back the whole snapshot rather than leaving an unkeyed row behind, and a
    # second run inside the same window sends nothing.
    try:
        snapshot = create_snapshot(db, preference.user_id, now=now,
                                   iso_week=iso_week(scheduled_day))
    except IntegrityError:
        db.rollback()
        return 'already_sent'

    if snapshot is None:
        # create_snapshot returns None for an empty forgotten set and writes
        # nothing in that case, so this tick simply tries again next time. It
        # does NOT claim the week, and must not: a save that becomes forgotten
        # later in the window still deserves its note.
        return 'nothing_to_show'

    if claimed is not None:
        claimed.append(snapshot)

    count = db.query(func.count(SnapshotItem.save_id)).filter(
        SnapshotItem.snapshot_id == snapshot.id).scalar()
    result = send_weekly_push(preference.user_id, snapshot.id, count)

    if result.delivered:
        # Someone heard it. Keeping the key is what stops a second run from
        # notifying the same person twice, and recording WHY it may be kept is
        # what lets the stale sweep tell this row from a stranded one.
        #
        # Read the clock now rather than reusing `now`: `now` is the tick's
        # instant, and this column exists to say when the note actually landed.
        # On a tick that spends a minute walking through users, the tick time is
        # wrong by that much for everyone after the first.
        snapshot.delivered_at = datetime.datetime.now(datetime.timezone.utc)
        db.commit()
        return 'sent'

    # Not delivered: FCM unconfigured, unusable credentials, no registered
    # device, or every device failing transiently. Release the claim so the
    # next run inside this window can try again once the cause is gone.
    log.info("weekly note: releasing the week claim for user %s (%s)",
             preference.user_id, result.reason)
    _release_claim(db, snapshot)
    return 'retry'


def run_weekly_note(db: Session, now: datetime.datetime | None = None,
                    budget_seconds: float | None = None) -> dict:
    """One tick: notify every enabled user whose chosen moment is now.

    Returns a summary so the task log and the tests can see what happened
    without reaching back into the database.

    Stops starting new users once [TASK_BUDGET_FRACTION] of the soft time limit
    is spent. Overrunning it does not fail loudly -- Celery raises
    `SoftTimeLimitExceeded` inside whatever happens to be running, which would
    kill the worker mid-transaction and strand whatever claim was open. Users
    not reached are simply still due, and the next tick picks them up while
    their window is open.

    `budget_seconds` is the test seam for that budget.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    summary = {'checked': 0, 'due': 0, 'sent': 0, 'retry': 0,
               'already_sent': 0, 'nothing_to_show': 0, 'failed': 0,
               'released_stale': 0, 'skipped_for_budget': 0}

    started = time.monotonic()
    budget = (budget_seconds if budget_seconds is not None
              else _soft_time_limit() * TASK_BUDGET_FRACTION)

    summary['released_stale'] = release_stale_claims(db, now)

    preferences = db.query(WeeklyNotePreference).filter(
        WeeklyNotePreference.enabled.is_(True)).all()
    for index, preference in enumerate(preferences):
        summary['checked'] += 1
        if not due_now(preference, now):
            continue
        summary['due'] += 1

        # Checked before a user is started, not after: the point is never to
        # OPEN a claim the worker may not get to finish.
        if time.monotonic() - started > budget:
            remaining = len(preferences) - index
            summary['skipped_for_budget'] = remaining
            log.warning("weekly note: %d of %d accounts left for the next tick "
                        "(%.0fs of %.0fs budget spent)", remaining,
                        len(preferences), time.monotonic() - started, budget)
            break

        # One account must never cost the others their note. The tick walks
        # every enabled user, so an unhandled exception here would skip every
        # user after this one and be re-run 15 minutes later with the same
        # result.
        claimed: list[WeeklySnapshot] = []
        try:
            outcome = _send_if_due(db, preference, now, claimed)
        except SoftTimeLimitExceeded:
            # Caught by the `except Exception` below unless it is named here.
            # The claim is committed, so it is released before re-raising: the
            # worker is about to be killed, and a claim held by a process that
            # never returns strands the account for the week.
            _release_claim_quietly(db, claimed)
            raise
        except Exception as exc:  # noqa: BLE001 - one user, then carry on
            # User id and exception type only: a provider error can quote the
            # user's own content, and this log is shipped and grepped.
            log.error("weekly note: user %s could not be processed: %s",
                      preference.user_id, _safe_error(exc))
            # The key was already committed, so without this the account is
            # silenced for the rest of the week over one bad exception.
            _release_claim_quietly(db, claimed)
            summary['failed'] += 1
            continue
        summary[outcome] += 1
    return summary
