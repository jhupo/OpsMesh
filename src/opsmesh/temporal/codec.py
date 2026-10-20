"""Encrypt sensitive Workflow and Activity payloads through Temporal's public codec."""

import base64
from collections.abc import Iterable

from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

from opsmesh.shared.security.encryption import SecretCipher

_ENCODING = b"binary/opsmesh-encrypted"


class EncryptedPayloadCodec(PayloadCodec):
    def __init__(self, encryption: SecretCipher) -> None:
        self._encryption = encryption

    async def encode(self, payloads: Iterable[Payload]) -> list[Payload]:
        result: list[Payload] = []
        for payload in payloads:
            encrypted = self._encryption.encrypt_payload(
                {"payload": base64.b64encode(payload.SerializeToString()).decode("ascii")}
            )
            result.append(
                Payload(
                    metadata={
                        "encoding": _ENCODING,
                        "key_id": encrypted.key_id.encode("utf-8"),
                    },
                    data=encrypted.ciphertext.encode("ascii"),
                )
            )
        return result

    async def decode(self, payloads: Iterable[Payload]) -> list[Payload]:
        result: list[Payload] = []
        for payload in payloads:
            if payload.metadata.get("encoding") != _ENCODING:
                raise ValueError("Temporal payload must use the configured encrypted codec")
            decoded = self._encryption.decrypt_payload(
                payload.data.decode("ascii"),
                key_id=payload.metadata["key_id"].decode("utf-8"),
            ).get("payload")
            if not isinstance(decoded, str):
                raise ValueError("Encrypted Temporal payload is malformed")
            result.append(Payload.FromString(base64.b64decode(decoded, validate=True)))
        return result
