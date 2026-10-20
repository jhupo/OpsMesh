"""Generated SDK through HTTP, real Redis/PG, Outbox and a native Temporal Worker."""

import asyncio
import os
import socket
import threading
import time
from uuid import uuid4

import httpx
import pytest
import uvicorn
from opsmesh_sdk.api.channels import (
    ChannelAccountCreate,
    ChannelConnectionChange,
    ChannelDeliveryCreate,
    create_channels_client,
)
from opsmesh_sdk.api.identity import LoginRequest, create_identity_client
from opsmesh_sdk.api.platform import PlatformOperationCreate, create_platform_client
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.errors import AuthorizationError, ConflictError
from sqlalchemy import select
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer, Worker

from opsmesh.bootstrap.api import create_app
from opsmesh.bootstrap.health import temporal_health_probe
from opsmesh.bootstrap.source_notifications import AuthoritativeEvents
from opsmesh.capabilities.models import CapabilityDefinition, CapabilityVersion
from opsmesh.extensions.models import Installation
from opsmesh.identity.provisioning import AccountDefaults, provision_first_admin
from opsmesh.orchestration.triggers.activities import TriggerActivities
from opsmesh.orchestration.triggers.workflow import SourceEventWorkflow
from opsmesh.platform.operations.activities import PlatformOperationActivities
from opsmesh.platform.operations.workflow import PlatformOperationWorkflow
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry
from opsmesh.temporal.dispatch.outbox import TemporalOutboxPublisher
from tests.conftest import build_test_settings
from tests.execution.test_session_routes import register_account


@pytest.fixture
def live_api(database_url, database_sessions):
    redis = os.environ.get("OPSMESH_TEST_REDIS_URL")
    temporal = os.environ.get("OPSMESH_TEST_TEMPORAL_ADDRESS")
    if not redis or not temporal:
        pytest.skip("Real Redis and Temporal endpoints are required")
    settings = build_test_settings(database_url).model_copy(
        update={
            "redis_url": redis,
            "redis_key_prefix": f"platform-test:{uuid4()}",
            "temporal_address": temporal,
            "credential_encryption_key_id": "test",
        }
    )
    with database_sessions() as session, session.begin():
        credentials = {"email": "admin@example.com", "password": "StrongFixturePassword123!"}
        provision_first_admin(
            session,
            email=credentials["email"],
            password=credentials["password"],
            display_name="Administrator",
            defaults=AccountDefaults(),
        )
    app = create_app(settings)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    address = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(
        target=lambda: asyncio.run(server.serve(sockets=[listener])), daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        yield address, app, credentials
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        assert not thread.is_alive()


def test_sdk_admin_admission_and_dependency_readiness(
    live_api, database_sessions, async_database_sessions
):
    address, app, credentials = live_api
    member = register_account(database_sessions, email="member-http@example.com")

    async def exercise():
        client = await Client.connect(
            os.environ["OPSMESH_TEST_TEMPORAL_ADDRESS"], data_converter=pydantic_data_converter
        )
        queue = f"platform-http-{uuid4()}"
        activities = PlatformOperationActivities(
            database_sessions, app.state.platform_operation_handlers
        )
        runtime = app.state.flow_activities
        triggers = TriggerActivities(
            runtime,
            AuthoritativeEvents(runtime.cipher, runtime.scopes),
            client,
            queue,
        )
        publisher = TemporalOutboxPublisher(
            async_database_sessions, client, task_queue=queue, cipher=app.state.secret_cipher
        )
        async with Worker(
            client,
            task_queue=queue,
            workflows=[PlatformOperationWorkflow, SourceEventWorkflow],
            activities=[activities.execute_page, activities.failed, triggers.consume],
        ):
            async with PlatformClient(address) as anonymous:
                login = await create_identity_client(anonymous).auth.login(
                    LoginRequest(email=credentials["email"], password=credentials["password"])
                )
            async with PlatformClient(address, session_id=str(login.session_token)) as channel:
                api = create_platform_client(channel)
                with database_sessions() as session, session.begin():
                    definition = CapabilityDefinition(
                        type="channel",
                        key="sdk-http",
                        name="SDK HTTP",
                        created_by_user_id=login.user.user_id,
                    )
                    session.add(definition)
                    session.flush()
                    version = CapabilityVersion(
                        definition_id=definition.id, version=1, status="published"
                    )
                    session.add(version)
                    session.flush()
                    installation = Installation(
                        version_id=version.id,
                        requested_by=login.user.user_id,
                        package_digest="a" * 64,
                        namespace="sdk-http",
                        status="enabled",
                    )
                    session.add(installation)
                    session.flush()
                    installation_id, version_id = installation.id, version.id
                channels = create_channels_client(channel)
                account = await channels.accounts.create(
                    ChannelAccountCreate(
                        installation_id=installation_id,
                        version_id=version_id,
                        channel_type="sdk-http",
                        external_account_id="bot",
                    )
                )
                assert "fixture-secret" not in account.model_dump_json()
                assert (await channels.accounts.get(account.id)).epoch == 1
                delivery = await channels.accounts.deliveries.create(
                    account.id,
                    ChannelDeliveryCreate(
                        idempotency_key="sdk-http",
                        conversation_key="private:one",
                        payload={"text": "hello"},
                    ),
                )
                assert delivery.status == "pending"
                await channels.accounts.connection.change(
                    account.id, ChannelConnectionChange(expected_epoch=1, enabled=False)
                )
                with pytest.raises(ConflictError):
                    await channels.accounts.connection.change(
                        account.id, ChannelConnectionChange(expected_epoch=1, enabled=True)
                    )
                for command, drained in (("admission.drain", True), ("admission.resume", False)):
                    receipt = await api.operations.submit(
                        PlatformOperationCreate(
                            owner="platform",
                            command=command,
                            target={},
                            idempotency_key=f"http:{command}",
                        )
                    )
                    assert receipt.status == "accepted"
                    assert await publisher.publish_pending(limit=50) >= 1
                    deadline = asyncio.get_running_loop().time() + 15
                    while (await api.operations.get(receipt.id)).status != "completed":
                        assert asyncio.get_running_loop().time() < deadline
                        await asyncio.sleep(0.05)
                    admission = await api.operations.admission()
                    assert admission.drained == drained
                    assert all(count == 0 for count in admission.active.values())
            async with PlatformClient(address, session_id=str(member)) as channel:
                with pytest.raises(AuthorizationError):
                    await create_platform_client(channel).operations.admission()
            async with httpx.AsyncClient(base_url=address) as http:
                assert (await http.get("/api/v1/health/ready")).status_code == 200

                app.state.temporal_health_probe = temporal_health_probe(
                    app.state.settings.model_copy(update={"temporal_address": "127.0.0.1:1"})
                )
                failure = await http.get("/api/v1/health/ready")
                assert failure.status_code == 503
                assert failure.json()["status"] == "temporal_unavailable"
            while await publisher.publish_pending(limit=50):
                pass
            with database_sessions() as session:
                source_intents = session.scalars(
                    select(TemporalOutboxEntry).where(
                        TemporalOutboxEntry.workflow_id.startswith("source-event:")
                    )
                ).all()
                assert source_intents
                assert all(intent.published_at is not None for intent in source_intents)
                source_ids = [intent.workflow_id for intent in source_intents]
            for workflow_id in source_ids:
                handle = client.get_workflow_handle(workflow_id)
                assert await asyncio.wait_for(handle.result(), 15) == {"accepted": 0}
                await Replayer(
                    workflows=[SourceEventWorkflow], data_converter=pydantic_data_converter
                ).replay_workflow(await handle.fetch_history())

    asyncio.run(exercise())
