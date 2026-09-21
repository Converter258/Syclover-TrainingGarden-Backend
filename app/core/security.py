from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

PASSWORD_ITERATIONS = 260_000


class InvalidTokenError(ValueError):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(candidate, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


def digest_flag(flag: str, secret_key: str) -> str:
    return hmac.new(secret_key.encode(), flag.strip().encode(), hashlib.sha256).hexdigest()


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_access_token(subject: str, role: str, secret_key: str, ttl_minutes: int) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": now + ttl_minutes * 60,
        "jti": secrets.token_hex(8),
    }
    encoded_header = _b64_encode(json.dumps(header, separators=(",", ":")).encode())
    encoded_payload = _b64_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{encoded_header}.{encoded_payload}"
    signature = hmac.new(secret_key.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64_encode(signature)}"


def decode_access_token(token: str, secret_key: str) -> dict[str, Any]:
    try:
        encoded_header, encoded_payload, encoded_signature = token.split(".")
        signing_input = f"{encoded_header}.{encoded_payload}"
        expected = hmac.new(secret_key.encode(), signing_input.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64_decode(encoded_signature)):
            raise InvalidTokenError("Invalid token signature")
        header = json.loads(_b64_decode(encoded_header))
        payload = json.loads(_b64_decode(encoded_payload))
        if header.get("alg") != "HS256" or int(payload.get("exp", 0)) <= int(time.time()):
            raise InvalidTokenError("Token has expired")
        if not payload.get("sub"):
            raise InvalidTokenError("Token subject is missing")
        return payload
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        if isinstance(exc, InvalidTokenError):
            raise
        raise InvalidTokenError("Malformed token") from exc
