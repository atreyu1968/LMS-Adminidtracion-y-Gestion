import base64
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .settings import get_settings


settings = get_settings()
KID = "lms-administracion-gestion-1"


def _b64url_int(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def ensure_private_key() -> Path:
    path = settings.key_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        path.write_bytes(pem)
        path.chmod(0o600)
    return path


def private_key_pem() -> bytes:
    return ensure_private_key().read_bytes()


def public_jwk() -> dict:
    key = serialization.load_pem_private_key(private_key_pem(), password=None)
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": KID,
        "n": _b64url_int(numbers.n),
        "e": _b64url_int(numbers.e),
    }


def sign_jwt(payload: dict, headers: dict | None = None) -> str:
    hdr = {"kid": KID, "typ": "JWT"}
    if headers:
        hdr.update(headers)
    return jwt.encode(payload, private_key_pem(), algorithm="RS256", headers=hdr)
