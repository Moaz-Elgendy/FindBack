import os
from jose import JWTError, jwt


def decode_access_token(token: str) -> dict:
    secret = os.getenv("SUPABASE_JWT_SECRET") or os.getenv("API_SECRET_KEY")
    if not secret:
        raise JWTError("JWT verification is not configured")
    audience = os.getenv("JWT_AUDIENCE", "authenticated")
    return jwt.decode(token, secret, algorithms=["HS256"], audience=audience, options={"verify_aud": bool(audience)})
