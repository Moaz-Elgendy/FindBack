from fastapi import APIRouter, Depends, HTTPException, Request
from jose import JWTError
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.services import identity
from app.services.auth_tokens import GUEST_PREFIX, decode_access_token, issue_guest_token

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/guest")
def create_guest(request: Request, db: Session = Depends(get_db)):
    from app.services.capacity import guest_creation_limit
    guest_creation_limit(request.client.host if request.client else "unknown")
    try:
        token, expires = issue_guest_token()
        claims = decode_access_token(token)
    except JWTError as exc:
        raise HTTPException(503, "Guest authentication unavailable") from exc
    user, _ = identity.resolve_user(db, claims)
    return {"access_token": token, "expires_at": expires.isoformat(), "user_id": str(user.id)}


@router.get("/me")
def current_identity(user=Depends(get_current_user)):
    return {"id": str(user.id), "auth_subject": user.auth_subject,
            "email": user.email, "is_guest": bool(user.auth_subject and user.auth_subject.startswith(GUEST_PREFIX))}
