import hashlib
import hmac
import secrets


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str | None) -> bool:
    if encoded is None:
        hashlib.pbkdf2_hmac("sha256", password.encode(), b"opsmesh-login-v1", 600_000)
        return False
    scheme, iterations, salt, expected = encoded.split("$")
    if scheme != "pbkdf2_sha256":
        raise ValueError("Unsupported password hash")
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(actual.hex(), expected)
