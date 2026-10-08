"""Executable mitigations for unresolved JWT dependency advisories."""
from datetime import datetime, timedelta, timezone
import pytest
from jose import jwk, jwt, JWTError
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from jose.backends.cryptography_backend import CryptographyECKey
from app.services.auth_tokens import decode_access_token


def test_supabase_rejects_der_public_key_hmac_confusion(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://example.supabase.co')
    public = ec.generate_private_key(ec.SECP256R1()).public_key()
    der = public.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    token = jwt.encode({'sub': 'victim', 'iss': 'https://example.supabase.co/auth/v1',
                        'aud': 'authenticated', 'exp': datetime.now(timezone.utc) + timedelta(minutes=5)},
                       der, algorithm='HS256', headers={'kid': 'claimed-key'})
    with pytest.raises(JWTError, match='algorithm'):
        decode_access_token(token)


def test_es256_uses_cryptography_not_ecdsa_signing_backend():
    private = ec.generate_private_key(ec.SECP256R1())
    assert isinstance(jwk.construct(private, algorithm='ES256'), CryptographyECKey)
