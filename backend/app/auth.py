import os

from fastapi import Depends, Header, HTTPException
from jose import JWTError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services import identity
from app.services.auth_tokens import decode_access_token


def get_current_user(authorization: str | None = Header(None), db: Session = Depends(get_db)) -> User:
    """Verify JWTs in production; permit an explicit local-only dev identity.

    A plain `def` on purpose: FastAPI runs it in a worker thread. As `async def`
    it ran on the event loop and its synchronous queries (`resolve_user`)
    stalled every other request while Postgres answered.

    Phase 16: the token is still verified exactly as before, by
    `decode_access_token`. What changed is only which claim identifies the user
    afterwards -- the stable subject rather than the reassignable address. See
    app/services/identity.py for the branches.
    """
    dev_auth = os.getenv("DEV_AUTH_ENABLED", "false").lower() == "true"
    if not authorization:
        if not dev_auth:
            raise HTTPException(status_code=401, detail="Bearer token required")
        return identity.dev_user(db)

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    try:
        claims = decode_access_token(token)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid access token") from exc
    user, _outcome = identity.resolve_user(db, claims)
    return user
