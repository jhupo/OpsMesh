"""Real Temporal execution and replay of a PostgreSQL-backed platform command."""

import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer, Worker

from opsmesh.bootstrap.platform import operation_handlers
from opsmesh.identity.users.service import create_user, identity_of
from opsmesh.platform.operations import service
from opsmesh.platform.operations.activities import PlatformOperationActivities
from opsmesh.platform.operations.admission import is_open
from opsmesh.platform.operations.models import PlatformOperation
from opsmesh.platform.operations.workflow import (
    PlatformOperationExecution,
    PlatformOperationWorkflow,
)
from opsmesh.shared.security.encryption import SecretCipher

CIPHER = SecretCipher(key="JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0=", key_id="test")


@pytest.mark.parametrize("batch_failure", [False, True])
def test_real_operation_execution_and_replay(database_sessions, batch_failure):
    address = os.environ.get("OPSMESH_TEST_TEMPORAL_ADDRESS")
    if not address:
        pytest.skip("OPSMESH_TEST_TEMPORAL_ADDRESS is not configured")
    with database_sessions() as session, session.begin():
        actor = identity_of(
            create_user(
                session,
                email="temporal-admin@example.com",
                display_name="Admin",
                platform_roles=frozenset({"admin"}),
            )
        )
        operation = service.submit(
            session,
            owner="platform",
            command="admission.drain",
            target={},
            actor=actor,
            idempotency_key="drain-real",
            handlers=operation_handlers(CIPHER),
        )
        identifier = operation.id

    async def verify():
        client = await Client.connect(address, data_converter=pydantic_data_converter)
        queue = f"platform-test-{uuid4()}"
        handlers = operation_handlers(CIPHER)
        if batch_failure:

            def fail(*args):
                raise RuntimeError("Injected owner batch failure")

            handlers[("platform", "admission.drain")] = fail
        activities = PlatformOperationActivities(database_sessions, handlers)
        async with Worker(
            client,
            task_queue=queue,
            workflows=[PlatformOperationWorkflow],
            activities=[activities.execute_page, activities.failed],
        ):
            handle = await client.start_workflow(
                PlatformOperationWorkflow.run,
                PlatformOperationExecution(
                    execution_id=identifier, workflow_id=f"platform-operation:{identifier}"
                ),
                id=f"platform-operation:{identifier}",
                task_queue=queue,
                execution_timeout=timedelta(seconds=40),
            )
            assert await handle.result() == ("needs_attention" if batch_failure else "completed")
            history = await handle.fetch_history()
        with database_sessions() as session:
            assert is_open(session) == batch_failure
            if batch_failure:
                operation = session.get(PlatformOperation, identifier)
                assert operation.failure_code == "batch_execution_failed"
        await Replayer(
            workflows=[PlatformOperationWorkflow], data_converter=pydantic_data_converter
        ).replay_workflow(history)

    asyncio.run(verify())
