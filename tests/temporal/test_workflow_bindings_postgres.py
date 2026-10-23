"""Workflow bindings accept private session references while rejecting raw secrets."""

from uuid import uuid4

import pytest

from opsmesh.agents.config.agent import AgentConfig
from opsmesh.agents.manager import AgentDefinition
from opsmesh.bootstrap.policies import PLATFORM_REDACTION
from opsmesh.execution.instances.schemas import InstanceCreate as AgentInstanceCreate
from opsmesh.execution.instances.service import create_instance as create_agent_instance
from opsmesh.execution.sessions.service import create_session
from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.users import service as users
from opsmesh.workflows.contracts import AgentBinding, ToolBinding, WorkflowBindings, WorkflowSpec
from opsmesh.workflows.instances.schemas import InstanceCreate
from opsmesh.workflows.instances.service import create_instance
from opsmesh.workflows.publication import publish_workflow
from opsmesh.workflows.schemas import WorkflowCreate
from opsmesh.workflows.service import create_workflow


def test_agent_session_binding_is_persisted_and_idempotent(database_sessions):
    cipher = SecretCipher(key="JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0=", key_id="test")
    with database_sessions.begin() as session:
        profile = users.create_account(
            session, email="workflow@example.test", display_name="Workflow", password="password-123"
        )
        actor = users.load_identity(session, profile.id)
        definition = AgentDefinition(id="assistant", config=AgentConfig(name="Assistant"))
        agent = create_agent_instance(
            session,
            actor=actor,
            request=AgentInstanceCreate(agent_id="assistant", request_key="agent"),
            load=lambda _: definition,
            cipher=cipher,
        )
        conversation = create_session(session, actor=actor, instance_id=agent.id)
        workflow = create_workflow(
            session,
            actor=actor,
            request=WorkflowCreate(
                name="Agent binding",
                spec=WorkflowSpec.model_validate(
                    {
                        "graph": {
                            "nodes": [{"key": "answer", "kind": "agent", "binding": "assistant"}]
                        }
                    }
                ),
            ),
            redaction=PLATFORM_REDACTION,
        )
        revision = publish_workflow(
            session,
            actor=actor,
            workflow_id=workflow.id,
            expected_revision=workflow.revision,
            visibility="private",
            redaction=PLATFORM_REDACTION,
        )
        request = InstanceCreate(
            revision_id=revision.id,
            name="Bound Agent",
            request_key="workflow-instance",
            bindings=WorkflowBindings(
                agents={"assistant": AgentBinding(instance_id=agent.id, session_id=conversation.id)}
            ),
        )
        first = create_instance(
            session, actor=actor, request=request, cipher=cipher, redaction=PLATFORM_REDACTION
        )
        second = create_instance(
            session, actor=actor, request=request, cipher=cipher, redaction=PLATFORM_REDACTION
        )
        assert first.id == second.id
        frozen = cipher.decrypt_payload(first.encrypted_prepared)
        assert frozen["bindings"]["agents"]["assistant"]["session_id"] == str(conversation.id)


def test_tool_binding_still_rejects_literal_credentials(database_sessions):
    cipher = SecretCipher(key="JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0=", key_id="test")
    with database_sessions.begin() as session:
        profile = users.create_account(
            session, email="binding@example.test", display_name="Binding", password="password-123"
        )
        actor = users.load_identity(session, profile.id)
        request = InstanceCreate(
            revision_id=uuid4(),
            name="Invalid secret",
            request_key="secret",
            bindings=WorkflowBindings(
                tools={
                    "math": ToolBinding(
                        kind="plugin",
                        resource_id="math",
                        allowed_operations=["add"],
                        config={"api_key": "literal"},
                    )
                }
            ),
        )
        with pytest.raises(InvalidArgumentError, match="credential references"):
            create_instance(
                session, actor=actor, request=request, cipher=cipher, redaction=PLATFORM_REDACTION
            )
