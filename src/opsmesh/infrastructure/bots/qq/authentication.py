"""QQ callback authentication and bounded challenge signing.

Protocol: https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/sign.html
"""

import re
import time
from collections.abc import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from opsmesh.framework.adapters import BotWebhookRequest
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import JsonData


class QQWebhookAuthenticator:
    def __init__(
        self, secret: str, *, max_age_seconds: int = 300, clock: Callable[[], float] = time.time
    ) -> None:
        material = secret.encode("utf-8")
        if not material:
            raise OpsMeshError("CONFIG_INVALID", "QQ client secret cannot be empty")
        seed = (material * ((32 + len(material) - 1) // len(material)))[:32]
        self._private_key = Ed25519PrivateKey.from_private_bytes(seed)
        self._max_age_seconds, self._clock = max_age_seconds, clock

    def _timestamp(self, value: object) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,12}", value):
            raise OpsMeshError("UNAUTHORIZED", "QQ callback timestamp is invalid")
        if abs(self._clock() - int(value)) > self._max_age_seconds:
            raise OpsMeshError(
                "UNAUTHORIZED", "QQ callback timestamp is outside the allowed window"
            )
        return value

    def verify(self, request: BotWebhookRequest) -> None:
        timestamp = self._timestamp(request.header("X-Signature-Timestamp"))
        signature = request.header("X-Signature-Ed25519")
        try:
            if signature is None or not re.fullmatch(r"[0-9a-fA-F]{128}", signature):
                raise ValueError("Invalid signature encoding")
            self._private_key.public_key().verify(
                bytes.fromhex(signature), timestamp.encode("ascii") + request.body
            )
        except (ValueError, InvalidSignature) as error:
            raise OpsMeshError("UNAUTHORIZED", "Invalid QQ callback signature") from error

    def challenge(self, payload: JsonData) -> JsonData:
        timestamp = self._timestamp(payload.get("event_ts"))
        token = payload.get("plain_token")
        # QQ's unsigned registration challenge is narrowly scoped. Accepting arbitrary
        # strings here would let a caller sign the bytes of an ordinary JSON event.
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", token):
            raise OpsMeshError("INVALID_REQUEST", "QQ challenge token has an invalid format")
        signature = self._private_key.sign((timestamp + token).encode("ascii"))
        return {"plain_token": token, "signature": signature.hex()}
