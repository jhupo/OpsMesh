"""Real-Temporal verification for the encrypted payload codec.

Requires OPSMESH_TEST_TEMPORAL_ADDRESS; skipped otherwise. Must be executed
against a real Temporal server (never mocked): a workflow carrying a secret
input runs through the codec, the stored history must contain no plaintext,
and the raw history must replay deterministically.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from temporalio.client import Client, WorkflowHistory
from temporalio.converter import DataConverter
from temporalio.worker import Replayer, Worker

from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.temporal.codec import EncryptedPayloadCodec
from tests.temporal.codec_verification_workflows import TASK_QUEUE, EchoWorkflow, echo_activity

TEMPORAL_ADDRESS_ENV = "OPSMESH_TEST_TEMPORAL_ADDRESS"
TEST_KEY = "JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0="
PLAINTEXT_SECRET = "codec-verification-secret-value"
ENCRYPTED_ENCODING_MARKER = b"binary/opsmesh-encrypted"


def temporal_address() -> str:
    address = os.environ.get(TEMPORAL_ADDRESS_ENV, "").strip()
    if not address:
        pytest.skip(f"{TEMPORAL_ADDRESS_ENV} is not configured")
    return address


def test_encrypted_history_has_no_plaintext_and_replays_on_real_temporal() -> None:
    address = temporal_address()

    async def run() -> None:
        cipher = SecretCipher(key=TEST_KEY, key_id="primary")
        converter = DataConverter(payload_codec=EncryptedPayloadCodec(cipher))
        client = await Client.connect(address, data_converter=converter)
        workflow_id = f"codec-verification-{uuid4()}"
        async with Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[EchoWorkflow],
            activities=[echo_activity],
        ):
            handle = await client.start_workflow(
                EchoWorkflow.run,
                {"credential": PLAINTEXT_SECRET},
                id=workflow_id,
                task_queue=TASK_QUEUE,
            )
            result = await handle.result()
            assert result == {"credential": PLAINTEXT_SECRET}
            history = await handle.fetch_history()

        raw_history = b"".join(event.SerializeToString() for event in history.events)
        assert PLAINTEXT_SECRET.encode("utf-8") not in raw_history
        assert ENCRYPTED_ENCODING_MARKER in raw_history

        replayer = Replayer(workflows=[EchoWorkflow], data_converter=converter)

        async def histories() -> AsyncIterator[WorkflowHistory]:
            yield WorkflowHistory(workflow_id=workflow_id, events=history.events)

        replay_results = await replayer.replay_workflows(histories())
        assert not replay_results.replay_failures

    asyncio.run(run())
