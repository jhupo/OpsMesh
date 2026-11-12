"""Ed25519 verification for QQ official bot webhook callbacks."""

from __future__ import annotations

import binascii

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .errors import QQConfigurationError

_KEY_SIZE = 32
_SIGNATURE_SIZE = 64


class QQSigner:
    """Verify callback signatures and sign the URL validation challenge."""

    def __init__(self, secret: str) -> None:
        """Derive the bot key pair from the configured secret seed.

        Args:
            secret: The QQ bot secret used as the Ed25519 seed.

        Raises:
            QQConfigurationError: The secret is not a 32-byte seed.
        """
        seed = secret.encode("utf-8")
        if len(seed) != _KEY_SIZE:
            raise QQConfigurationError(
                "The QQ bot secret must be exactly 32 bytes"
            )
        self._private = Ed25519PrivateKey.from_private_bytes(seed)
        self._public: Ed25519PublicKey = self._private.public_key()

    def verify(self, signature: str, timestamp: str, body: bytes) -> bool:
        """Check a hexadecimal callback signature over the timestamped body."""
        try:
            raw = bytes.fromhex(signature)
        except (ValueError, binascii.Error):
            return False
        if len(raw) != _SIGNATURE_SIZE:
            return False
        try:
            self._public.verify(raw, timestamp.encode("utf-8") + body)
        except InvalidSignature:
            return False
        return True

    def sign(self, message: str) -> str:
        """Sign a validation challenge and return lowercase hexadecimal."""
        return self._private.sign(message.encode("utf-8")).hex()
