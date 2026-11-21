from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from lekha.core.config import get_settings

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Keyed hash so a database leak does not reveal usable session/link tokens."""
    key = get_settings().secret_key.encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def new_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, hash). Only the hash is stored."""
    prefix = "lk_" + secrets.token_hex(4)
    full = f"{prefix}_{secrets.token_urlsafe(24)}"
    return full, prefix, hash_token(full)


def constant_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def password_problems(password: str) -> list[str]:
    problems = []
    if len(password) < 10:
        problems.append("Use at least 10 characters.")
    if password.lower() == password or password.upper() == password:
        problems.append("Mix upper- and lower-case letters.")
    if not any(c.isdigit() for c in password):
        problems.append("Include at least one digit.")
    return problems
