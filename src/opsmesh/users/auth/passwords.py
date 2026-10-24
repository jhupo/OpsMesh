"""Password hashing and verification parameters."""

from __future__ import annotations

from typing import Final

from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError
from pwdlib.hashers.argon2 import Argon2Hasher

MIN_PASSWORD_LENGTH: Final = 8
MAX_PASSWORD_LENGTH: Final = 256

_password_hash = PasswordHash((Argon2Hasher(),))


def validate_password_shape(password: str) -> None:
    """Reject unusable passwords before any hashing work happens."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"A password needs at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"A password may not exceed {MAX_PASSWORD_LENGTH} characters")


def hash_password(password: str) -> str:
    """Hash one password with the configured Argon2 parameters."""
    validate_password_shape(password)
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a candidate password in constant time against the stored hash."""
    try:
        return _password_hash.verify(password, password_hash)
    except (PwdlibError, ValueError):
        # A malformed stored hash is a failed verification, never a crash.
        return False
