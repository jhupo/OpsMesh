import asyncio
from dataclasses import replace

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter

from opsmesh.runtime.temporal.codec import EncryptedPayloadCodec
from opsmesh.shared.security.secrets import SecretEncryptionService


def test_temporal_payload_is_encrypted_and_can_replay_after_key_rotation() -> None:
    async def verify() -> None:
        before = replace(
            pydantic_data_converter,
            payload_codec=EncryptedPayloadCodec(
                SecretEncryptionService(secret="previous-secret", key_id="previous")
            ),
        )
        payloads = await before.encode([{"input": "private tenant content"}])
        assert b"private tenant content" not in payloads[0].SerializeToString()
        after = replace(
            pydantic_data_converter,
            payload_codec=EncryptedPayloadCodec(
                SecretEncryptionService(
                    secret="current-secret",
                    key_id="current",
                    previous_secrets={"previous": "previous-secret"},
                )
            ),
        )
        assert await after.decode(payloads) == [{"input": "private tenant content"}]
        unrelated = replace(
            pydantic_data_converter,
            payload_codec=EncryptedPayloadCodec(
                SecretEncryptionService(secret="unrelated", key_id="other")
            ),
        )
        with pytest.raises(ValueError, match="could not be decrypted"):
            await unrelated.decode(payloads)
        clear = await pydantic_data_converter.encode([{"input": "private tenant content"}])
        with pytest.raises(ValueError, match="encrypted codec"):
            await after.decode(clear)

    asyncio.run(verify())
