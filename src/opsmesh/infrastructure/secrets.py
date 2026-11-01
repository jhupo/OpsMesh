"""Resolve deployment credentials without putting their values in resource records."""

import os
import re
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from opsmesh.framework.errors import OpsMeshError


class DeploymentSecretResolver:
    def __init__(self, values: Mapping[str, SecretStr] | None = None) -> None:
        self._values = dict(values or {})
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name) for name in self._values):
            raise OpsMeshError("CONFIG_INVALID", "Invalid runtime credential name")

    def resolve(self, reference: str) -> str:
        if reference.startswith("runtime:"):
            secret = self._values.get(reference[8:])
            if secret is None or not secret.get_secret_value():
                raise OpsMeshError("configuration_missing", "Runtime credential is unavailable")
            return secret.get_secret_value()
        if not re.fullmatch(r"env:[A-Za-z_][A-Za-z0-9_]*", reference):
            raise OpsMeshError("invalid_request", "Unsupported deployment credential reference")
        value = os.environ.get(reference[4:])
        if not value:
            raise OpsMeshError(
                "configuration_missing", "The configured provider credential is unavailable"
            )
        return value


class FileCredentialCipher:
    """Encrypt database credentials with a deployment key stored outside the database."""

    def __init__(self, key_path: Path) -> None:
        self.key_path = key_path

    def _cipher(self, *, create: bool = False) -> Fernet:
        path = self.key_path
        if create and not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = path.with_name(f".{path.name}.{uuid4().hex}")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
                with suppress(FileExistsError):
                    os.link(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        try:
            if path.is_symlink() or (os.name != "nt" and path.stat().st_mode & 0o077):
                raise ValueError("Insecure credential key")
            return Fernet(path.read_bytes())
        except (OSError, ValueError):
            raise OpsMeshError(
                "CREDENTIAL_KEY_UNAVAILABLE", "Deployment credential key unavailable"
            ) from None

    def encrypt(self, data: bytes) -> str:
        return self._cipher(create=True).encrypt(data).decode("ascii")

    def decrypt(self, ciphertext: str) -> bytes:
        try:
            return self._cipher().decrypt(ciphertext.encode("ascii"))
        except (InvalidToken, ValueError, UnicodeError):
            raise OpsMeshError(
                "CREDENTIAL_INVALID", "Stored credential could not be decrypted"
            ) from None
