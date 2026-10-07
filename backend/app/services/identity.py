"""Which identity resolution a login goes through (Phase 16).

Identity used to be `users.email == token["email"]`. An address is reassignable,
so that handed the previous user's whole library to whoever acquired the address
next. Identity is now keyed on `users.auth_subject`, the token's `sub` claim,
which the issuer guarantees is stable.

Five outcomes, all decided here:

    ALREADY_BOUND   the subject is known; the row is used as-is
    BOUND_ON_LOGIN  a legacy row with no subject is bound to this subject,
                    but ONLY when the token proves the address is verified
    CREATED         nothing matched and there is no collision; a row is made
    REFUSED         the address matches a DIFFERENT subject. That is a
                    recycled address, and it is the one case that must never
                    resolve to the old row
    DISPLAY_ONLY    the subject matched but the token's address would collide
                    with another user's unique email, so the old address is
                    kept and the login continues

Nothing here changes token signature verification, adds an endpoint, or adds a
role. `decode_access_token` is still the only thing that turns a string into
claims.

Why the verified-email gate exists
----------------------------------
Binding a subject to a legacy row means trusting that the person presenting the
token is the one who owned that address before. If the address has been recycled
since the row was created, the newcomer would inherit the original owner's
library -- S1 again, through the back door. The provider's answer to "is this
address verified" is what makes the bind safe.

So the gate fails CLOSED. An absent claim means an unverified signal, and an
unverified signal means no bind. That is deliberately the inconvenient answer:
it makes the legacy path unavailable by default rather than making it
exploitable by default.
"""
from __future__ import annotations

import logging
import os

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User
from app.services.privacy import digest

log = logging.getLogger("findback.auth")

# Where the verified-email signal is read from, as a dotted claim path.
#
# `app_metadata` and NOT `user_metadata`: Supabase lets a user write their own
# `user_metadata`, so a flag read from there is set by the very person trying to
# claim someone else's row. `app_metadata` is admin-controlled and a user cannot
# write it. `auth.py` enforces this and tests/test_phase16_multitenant.py proves
# it by presenting user_metadata.email_verified=true and watching the bind fail.
DEFAULT_VERIFIED_CLAIM = "app_metadata.email_verified"

# The dev identity is never bound to a subject: it exists so a developer can run
# the app with no auth provider at all, and it must keep working unchanged.
DEV_DISPLAY_EMAIL = "dev@findback.local"


class Outcome:
    """Which branch a login took. Names rather than booleans, so a caller cannot
    half-read a decision, and so the log line is self-describing."""

    ALREADY_BOUND = "already_bound"
    BOUND_ON_LOGIN = "bound_on_login"
    CREATED = "created"
    REFUSED = "refused_recycled_address"
    DISPLAY_ONLY = "kept_old_display_email"


def verified_claim_path() -> str:
    return os.getenv("AUTH_EMAIL_VERIFIED_CLAIM") or DEFAULT_VERIFIED_CLAIM


def claim_value(claims: dict, path: str):
    """Read a dotted claim path, or None if any step is missing.

    Deliberately refuses to read `user_metadata`: it is user-writable in
    Supabase, so it cannot be evidence of anything. See the module docstring.
    """
    if path == "user_metadata" or path.startswith("user_metadata."):
        raise ValueError(
            "AUTH_EMAIL_VERIFIED_CLAIM must not point into user_metadata: "
            "users can write that themselves, so it proves nothing")
    node = claims
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
        if node is None:
            return None
    return node


# Values that mean "not verified" when the claim is a string. A bare
# truthiness check would call the string "false" verified, which is the one
# answer that must never be got wrong here.
_NOT_VERIFIED_WORDS = frozenset({"", "false", "0", "no", "null", "none",
                                 "undefined", "unverified"})


def email_is_verified(claims: dict) -> bool:
    """Whether the token proves this address is verified. Fails closed.

    Absent means unverified, and so does any value whose type this code does
    not recognise: the claim path is configurable, so an operator may point it
    at a boolean, at a timestamp, or at something unexpected, and an
    unrecognised shape is not evidence.
    """
    value = claim_value(claims, verified_claim_path())
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        # Covers both a boolean-in-a-string and a timestamp such as
        # "2026-01-01T00:00:00Z", which is itself proof of confirmation.
        return value.strip().lower() not in _NOT_VERIFIED_WORDS
    return False


def _masked(email: str | None) -> str:
    """A log-safe stand-in for an address. Never the address itself.

    S3 keys, tokens and page text are already filtered by main.py; an email is
    personal data and belongs in the same category, so only a short digest and
    the length reach the log.
    """
    if not email:
        return "none"
    return f"[user {digest(email)} {len(email)} chars]"


def dev_user(db: Session) -> User:
    """The local-only identity used when no token is presented.

    Unchanged in behaviour from before Phase 16, and deliberately NOT given a
    subject: there is no issuer behind it, so there is nothing stable to bind.
    """
    email = os.getenv("DEV_AUTH_EMAIL") or DEV_DISPLAY_EMAIL
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, auth_provider="dev")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def resolve_user(db: Session, claims: dict) -> tuple[User, str]:
    """The user these claims belong to, and which branch produced it.

    Raises HTTPException(401) for a recycled address rather than returning the
    row that owns the address. 401 and not 403 on purpose: 403 would confirm
    that the address is registered here, which is itself an answer the caller
    should not get.
    """
    subject = claims.get("sub")
    if not subject:
        raise HTTPException(status_code=401, detail="Token subject missing")
    # Guest identity is server-issued and cannot bind an existing email row.
    from app.services.auth_tokens import GUEST_ISSUER, GUEST_PREFIX
    if subject.startswith(GUEST_PREFIX):
        if claims.get("iss") != GUEST_ISSUER or claims.get("purpose") != "guest":
            raise HTTPException(status_code=401, detail="Invalid guest identity")
        claims = {"sub": subject, "iss": GUEST_ISSUER}
    email = claims.get("email") or f"{subject}@findback.local"
    provider = claims.get("iss", "jwt")

    # 1. The subject decides, if we already know it. This is the whole fix: from
    #    here on, an address that changes hands cannot move a library.
    user = db.query(User).filter(User.auth_subject == subject).first()
    if user is not None:
        _refresh_display_email(db, user, email)
        return user, Outcome.ALREADY_BOUND

    # 2. A legacy row: same address, no subject yet. Bind only on a verified
    #    signal.
    legacy = db.query(User).filter(
        User.email == email, User.auth_subject.is_(None)).first()
    if legacy is not None:
        if not email_is_verified(claims):
            log.warning(
                "[auth] legacy row not bound: no verified-email claim "
                "(path=%s user_id=%s email=%s)", verified_claim_path(),
                legacy.id, _masked(email))
            # No bind AND no new row: the address already belongs to somebody
            # here, so creating a second one would collide on users.email.
            raise HTTPException(
                status_code=401,
                detail="Cannot bind this account: the token does not prove "
                       "the address is verified")
        legacy.auth_subject = subject
        legacy.auth_provider = provider
        db.commit()
        db.refresh(legacy)
        log.info("[auth] bound legacy account to subject: user_id=%s email=%s",
                 legacy.id, _masked(email))
        return legacy, Outcome.BOUND_ON_LOGIN

    # 3. No subject, and no legacy row for this address. Either a brand new
    #    user, or an address that belongs to a DIFFERENT subject already.
    holder = db.query(User).filter(User.email == email).first()
    if holder is not None:
        log.error(
            "[auth] refused login: address is held by a different subject "
            "(user_id=%s email=%s). Treating as a recycled address.",
            holder.id, _masked(email))
        raise HTTPException(
            status_code=401,
            detail="This address is registered to a different account")

    # 4. A real new user. users.email is UNIQUE, which is why step 3 had to be
    #    checked rather than relying on the insert to fail.
    user = User(email=email, auth_subject=subject, auth_provider=provider)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Two first logins for one subject raced, and the other one committed
        # between the checks above and this insert. Its row is the correct
        # answer for both logins, so adopt it instead of failing the loser.
        db.rollback()
        winner = db.query(User).filter(User.auth_subject == subject).first()
        if winner is None:
            raise
        log.info("[auth] first-login race resolved: user_id=%s email=%s",
                 winner.id, _masked(email))
        return winner, Outcome.ALREADY_BOUND
    db.refresh(user)
    log.info("[auth] created account: user_id=%s email=%s", user.id, _masked(email))
    return user, Outcome.CREATED


def _refresh_display_email(db: Session, user: User, email: str) -> None:
    """Follow the address to the provider without ever losing the row.

    `email` is display-only now, but a stale one is its own small problem: the
    user would not recognise their own account. So it is updated when the change
    is safe.

    It is NOT updated when the new address already belongs to somebody else.
    `users.email` is UNIQUE NOT NULL, so writing it would either fail the whole
    login or, worse, tempt someone into dropping the constraint. Neither is
    acceptable for a cosmetic field: keep the old address, log it, carry on. The
    row is already correctly identified by its subject, so this costs the user
    nothing but a stale label.
    """
    if not email or user.email == email:
        return
    taken = db.query(User).filter(
        User.email == email, User.id != user.id).first()
    if taken is not None:
        log.warning(
            "[auth] kept the old display email: the provider's address for "
            "this subject is held by another account (user_id=%s email=%s)",
            user.id, _masked(email))
        return
    user.email = email
    db.commit()
    log.info("[auth] display email updated: user_id=%s email=%s",
             user.id, _masked(email))
