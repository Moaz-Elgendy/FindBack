import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen

from jose import JWTError, jwt

GUEST_ISSUER = "findback:guest"
GUEST_PREFIX = "guest:"
_jwks_cache = {}
_jwks_lock = threading.Lock()


def guest_retention_hours() -> int:
    return max(1, int(os.getenv("GUEST_RETENTION_HOURS", "24")))


def issue_guest_token() -> tuple[str, datetime]:
    secret = os.getenv("API_SECRET_KEY")
    if not secret:
        raise JWTError("Guest authentication is not configured")
    expires = datetime.now(timezone.utc) + timedelta(hours=guest_retention_hours())
    claims = {"sub": GUEST_PREFIX + str(uuid.uuid4()), "iss": GUEST_ISSUER,
              "aud": "findback:guest", "purpose": "guest", "exp": expires}
    return jwt.encode(claims, secret, algorithm="HS256"), expires


def _supabase_key(url: str, kid: str) -> dict:
    with _jwks_lock:
        cached = _jwks_cache.get(url)
        fresh = cached is not None and cached[0] > time.monotonic()
        keys = cached[1] if fresh else []
        key = next((k for k in keys if k.get("kid") == kid), None)
        if key is None:
            try:
                with urlopen(url + "/auth/v1/.well-known/jwks.json", timeout=5) as response:
                    keys = json.loads(response.read(65537))["keys"]
                if not isinstance(keys, list) or not all(isinstance(k, dict) for k in keys):
                    raise ValueError("Invalid JWKS")
            except Exception as exc:
                raise JWTError("JWT verification unavailable") from exc
            _jwks_cache[url] = (time.monotonic() + 300, keys)
            key = next((k for k in keys if k.get("kid") == kid), None)
        if key is None or key.get("kty") != "EC" or key.get("crv") != "P-256" or key.get("alg", "ES256") != "ES256":
            raise JWTError("Unknown JWT signing key")
        return key


def decode_access_token(token: str) -> dict:
    header = jwt.get_unverified_header(token)
    unverified = jwt.get_unverified_claims(token)
    if unverified.get("iss") == GUEST_ISSUER:
        secret = os.getenv("API_SECRET_KEY")
        if not secret or header.get("alg") != "HS256":
            raise JWTError("Invalid guest token")
        claims = jwt.decode(token, secret, algorithms=["HS256"], audience="findback:guest",
                            issuer=GUEST_ISSUER, options={"require_exp": True, "require_sub": True})
        if claims.get("purpose") != "guest" or not claims["sub"].startswith(GUEST_PREFIX):
            raise JWTError("Invalid guest identity")
        try:
            uuid.UUID(claims["sub"][len(GUEST_PREFIX):])
        except ValueError as exc:
            raise JWTError("Invalid guest identity") from exc
        return claims
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    if url:
        if header.get("alg") != "ES256" or not isinstance(header.get("kid"), str) or not header["kid"]:
            raise JWTError("Invalid JWT algorithm or signing key")
        claims = jwt.decode(token, _supabase_key(url, header["kid"]), algorithms=["ES256"],
                            audience="authenticated", issuer=url + "/auth/v1",
                            options={"require_exp": True, "require_sub": True, "require_aud": True, "require_iss": True})
    else:
        secret = os.getenv("SUPABASE_JWT_SECRET") or os.getenv("API_SECRET_KEY")
        if not secret:
            raise JWTError("JWT verification is not configured")
        audience = os.getenv("JWT_AUDIENCE", "authenticated")
        claims = jwt.decode(token, secret, algorithms=["HS256"], audience=audience,
                            options={"verify_aud": bool(audience)})
    if not claims.get("sub") or claims["sub"].startswith(GUEST_PREFIX):
        raise JWTError("Invalid account subject")
    return claims
