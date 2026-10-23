"""Persistent Agent execution against real PostgreSQL, Temporal and model services."""

import asyncio
import os
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from opsmesh_sdk.identity import InvocationSource
from pydantic import SecretStr
from sqlalchemy import select
from temporalio.worker import Replayer, Worker

from opsmesh.agents.assembly import AssemblyBindings
from opsmesh.agents.config.agent import AgentConfig
from opsmesh.agents.config.model import ModelRef
from opsmesh.agents.manager import AgentDefinition
from opsmesh.agents.providers import ModelInfo, ProviderConfig, ProviderRegistry
from opsmesh.bootstrap.telemetry import configure_process_telemetry
from opsmesh.bootstrap.temporal import connect_temporal
from opsmesh.config import Settings
from opsmesh.execution.instances.models import AgentInstance
from opsmesh.execution.instances.schemas import InstanceCreate
from opsmesh.execution.instances.service import create_instance
from opsmesh.execution.runs.models import ExecutionEvent, ExecutionRun
from opsmesh.execution.runs.schemas import RunCreate
from opsmesh.execution.runs.service import submit_run
from opsmesh.execution.sessions.models import ExecutionSession
from opsmesh.execution.sessions.service import create_session
from opsmesh.execution.sessions.storage import native_session
from opsmesh.users.quotas.models import CAPACITY_KINDS
from opsmesh.users.service import create_account, current_user
from opsmesh.workflows.engines.temporal.agent_activities import (
    InstanceActivities,
    InstanceModelProvider,
)
from opsmesh.workflows.engines.temporal.agent_workflow import AgentInstanceWorkflow

pytestmark = pytest.mark.openai_smoke


@pytest.mark.parametrize("streaming", [False, True], ids=["ordinary", "streaming"])
def test_persistent_instance_executes_and_replays(database_sessions, tmp_path, streaming):
    required = (
        "OPSMESH_TEST_TEMPORAL_ADDRESS",
        "OPSMESH_TEST_MODEL_API_KEY",
        "OPSMESH_TEST_MODEL_BASE_URL",
        "OPSMESH_TEST_MODEL_NAME",
    )
    if any(not os.environ.get(name) for name in required):
        pytest.skip("Real Temporal and model services are not configured")
    cipher = database_sessions.kw["info"]["dispatch_cipher"]
    model_id = os.environ[required[3]]
    definition = AgentDefinition(
        id="acceptance",
        config=AgentConfig(
            name="Acceptance",
            instructions="Reply with the number 42 only.",
            model=ModelRef(provider_id="acceptance", model_id=model_id),
            model_settings={"max_tokens": 2048, "reasoning": {"effort": "medium"}},
        ),
    )
    providers = ProviderRegistry()
    providers.register(
        ProviderConfig(
            id="acceptance",
            name="Acceptance",
            base_url=os.environ[required[2]],
            credential_ref="env:OPSMESH_TEST_MODEL_API_KEY",
            timeout=150,
            request_timeout=180,
            max_retries=2,
            models=[ModelInfo(id=model_id)],
        )
    )

    async def credential(reference):
        assert reference == "env:OPSMESH_TEST_MODEL_API_KEY"
        return SecretStr(os.environ[required[1]])

    def bindings(context, prepared):
        return AssemblyBindings(providers=providers, credentials=credential)

    with database_sessions.begin() as session:
        user = create_account(
            session,
            email="temporal@example.com",
            display_name="Acceptance",
            password="acceptance-only-password-12345",
            capacity_limits={kind: None for kind in CAPACITY_KINDS},
        )
        user_id = user.id
        actor = current_user(session, user.id)
        instance = create_instance(
            session,
            actor=actor,
            request=InstanceCreate(agent_id=definition.id, request_key="instance"),
            load=lambda identifier: definition,
            cipher=cipher,
        )
        conversation = create_session(session, actor=actor, instance_id=instance.id)
        run = submit_run(
            session,
            actor=actor,
            cipher=cipher,
            source=InvocationSource(stream_output=streaming),
            request=RunCreate(
                instance_id=instance.id,
                session_id=conversation.id,
                driver="temporal",
                request_key="run",
                input="Reply with 42 only.",
                timeout_seconds=210,
            ),
        )
        instance_id, session_id, run_id = instance.id, conversation.id, run.id
        generation = conversation.generation

    async def scenario():
        activities = InstanceActivities(
            database_sessions,
            cipher,
            bindings=bindings,
            history_root=tmp_path,
            # This ordinary Agent has no sandbox operations; Docker is verified separately.
            sandbox=Mock(),
        )
        settings = Settings(temporal_address=os.environ[required[0]])
        configure_process_telemetry(settings)
        client = await connect_temporal(
            settings,
            model_provider=InstanceModelProvider(activities),
        )
        queue = f"instance-acceptance-{uuid4()}"
        async with Worker(
            client,
            task_queue=queue,
            workflows=[AgentInstanceWorkflow],
            activities=[
                activities.claim,
                activities.finish,
                activities.decision,
                activities.abandon,
                activities.history,
            ],
        ):
            handle = await client.start_workflow(
                AgentInstanceWorkflow.run,
                {"execution_id": str(run_id)},
                id=f"instance:{run_id}",
                task_queue=queue,
                execution_timeout=timedelta(minutes=5),
            )
            try:
                await asyncio.wait_for(handle.result(), timeout=270)
            except BaseException:
                await handle.cancel()
                raise
            history = await handle.fetch_history()
        with database_sessions.begin() as session:
            stored = session.get(ExecutionRun, run_id)
            assert stored.status == "completed", stored.error_code
            assert cipher.decrypt_payload(stored.encrypted_result) == {"output": "42"}
            assert session.get(AgentInstance, instance_id).status == "active"
            assert session.get(ExecutionSession, session_id).status == "active"
            events = list(
                session.scalars(select(ExecutionEvent).where(ExecutionEvent.run_id == run_id))
            )
            deltas = [
                cipher.decrypt_payload(event.encrypted_payload)["delta"]
                for event in events
                if event.kind == "model_text_delta"
            ]
            assert "".join(deltas) == ("42" if streaming else "")
        private_history = native_session(tmp_path, user_id, session_id, generation)
        try:
            assert len(await private_history.get_items()) >= 2
        finally:
            private_history.close()
        replayer = Replayer(
            workflows=[AgentInstanceWorkflow],
            data_converter=client.data_converter,
            plugins=client.config()["plugins"],
        )
        await replayer.replay_workflow(history)

    asyncio.run(scenario())
