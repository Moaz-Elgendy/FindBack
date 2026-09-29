import os

from fastapi import Depends, Header, HTTPException
from jose import JWTError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services.auth_tokens import decode_access_token


async def get_current_user(authorization: str | None = Header(None), db: Session = Depends(get_db)) -> User:
    """Verify JWTs in production; permit an explicit local-only dev identity."""
    dev_auth = os.getenv("DEV_AUTH_ENABLED", "false").lower() == "true"
    if not authorization:
        if not dev_auth:
            raise HTTPException(status_code=401, detail="Bearer token required")
        email = os.getenv("DEV_AUTH_EMAIL", "dev@findback.local")
        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(email=email, auth_provider="dev")
            db.add(user)
            db.commit()
            db.refresh(user)
        return user

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    try:
        claims = decode_access_token(token)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid access token") from exc
    subject = claims.get("sub")
    email = claims.get("email") or f"{subject}@findback.local"
    if not subject:
        raise HTTPException(status_code=401, detail="Token subject missing")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, auth_provider=claims.get("iss", "jwt"))
        db.add(user)
        db.commit()
        db.refresh(user)
    return user
