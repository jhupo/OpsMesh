"""A configured MCP keeps its own environment and rechecks current availability."""

from uuid import uuid4

import pytest

from opsmesh.bootstrap.orchestration import ConfigurationDirectories
from opsmesh.bootstrap.routers import ConfigurationExecutionScopeAdmission
from opsmesh.capabilities.models import CapabilityDefinition, CapabilityVersion
from opsmesh.identity.provisioning import UserIdentityDirectory
from opsmesh.identity.users.service import create_user
from opsmesh.orchestration.configurations.bindings.models import CapabilityBinding
from opsmesh.orchestration.configurations.schemas import (
    CapabilitySelection,
    ConfigurationCreate,
    ConfigurationSpec,
    ConfigurationUpdate,
)
from opsmesh.orchestration.configurations.service import (
    assemble_configuration,
    create_configuration,
    update_configuration,
)
from opsmesh.orchestration.definitions.schemas import (
    AgentGraphSpec,
    AgentTemplate,
    DefinitionCreate,
)
from opsmesh.orchestration.definitions.service import create_definition, publish_definition
from opsmesh.orchestration.execution.contracts import ModelConfiguration
from opsmesh.orchestration.runtime.models import Environment, MCPConnection
from opsmesh.platform.model_connections.service import create_connection, grant_connection
from opsmesh.shared.errors import ForbiddenError
from tests.conftest import dispatch_cipher


def test_mcp_uses_independent_environment_and_rejects_closed_environment(database_sessions):
    admission = ConfigurationExecutionScopeAdmission()
    with database_sessions() as session, session.begin():
        owner = create_user(
            session,
            email=f"{uuid4()}@example.test",
            display_name="Owner",
            platform_roles=frozenset({"admin"}),
        )
        actor = UserIdentityDirectory().load_identity(session, owner.id)
        model = create_connection(
            session,
            actor=actor,
            cipher=dispatch_cipher(),
            name="model",
            api_key="fixture-only",
            base_url="https://example.test/v1",
            default_model="test",
            models=["test"],
        )
        grant_connection(
            session, actor=actor, connection_id=model.id, user_id=owner.id, models=["test"]
        )
        definition = create_definition(
            session,
            actor=actor,
            request=DefinitionCreate(
                name="MCP analyst",
                kind="agent_graph",
                spec=AgentGraphSpec(
                    entry_agent="main",
                    agents={"main": AgentTemplate(name="Analyst", mcp=["probe"])},
                ),
            ),
        )
        published = publish_definition(
            session, actor=actor, definition_id=definition.id, expected_revision=1
        )
        configuration = create_configuration(
            session,
            actor=actor,
            request=ConfigurationCreate(name="Analyst", definition_revision_id=published.id),
            resources=ConfigurationDirectories(),
            admission=admission,
            idempotency_key="independent-mcp",
        )
        environments = [
            Environment(
                scope_id=configuration.id,
                principal_id=owner.id,
                owner_ref=f"configuration:{configuration.id}",
                status="ready",
                configuration={},
            )
            for _ in range(2)
        ]
        capability = CapabilityDefinition(
            type="mcp", key="probe", name="Probe", created_by_user_id=owner.id
        )
        session.add_all([capability, *environments])
        session.flush()
        agent_environment, mcp_environment = environments
        version = CapabilityVersion(
            definition_id=capability.id,
            version=1,
            status="published",
            digest="a" * 64,
            configuration={
                "transport": "stdio",
                "command": ["python", "-m", "probe"],
                "tools": [{"name": "probe"}],
            },
        )
        connection = MCPConnection(
            scope_id=configuration.id,
            principal_id=owner.id,
            owner_ref=f"configuration:{configuration.id}",
            name="probe",
            transport="stdio",
            environment_id=mcp_environment.id,
            status="ready",
            configuration={"command": ["python", "-m", "probe"], "environment": {}},
            tools=[{"name": "probe", "inputSchema": {"type": "object"}}],
        )
        session.add_all([version, connection])
        session.flush()
        binding = CapabilityBinding(
            owner_user_id=owner.id,
            created_by=owner.id,
            name="probe",
            version_id=version.id,
            version_digest=version.digest,
            configuration={"mcp_connection_id": str(connection.id)},
        )
        session.add(binding)
        session.flush()
        revision = update_configuration(
            session,
            actor=actor,
            configuration_id=configuration.id,
            request=ConfigurationUpdate(
                expected_revision=0,
                specification=ConfigurationSpec(
                    models={"default": ModelConfiguration(connection_id=model.id, name="test")},
                    capabilities={"probe": CapabilitySelection(binding_id=binding.id)},
                    environment_id=agent_environment.id,
                ),
            ),
            admission=admission,
        )
        request, frozen = assemble_configuration(
            session,
            actor=actor,
            configuration_id=configuration.id,
            revision=revision,
            session_id=uuid4(),
            input="Probe",
            admission=admission,
        )
        assert request.environment_id == agent_environment.id != mcp_environment.id
        assert request.agents["main"].mcp_connections[0].connection_id == connection.id
        assert frozen[str(binding.id)]["environment_id"] == str(mcp_environment.id)
        mcp_environment.status = "closed"
        session.flush()
        with pytest.raises(ForbiddenError, match="MCP environment is unavailable"):
            assemble_configuration(
                session,
                actor=actor,
                configuration_id=configuration.id,
                revision=revision,
                session_id=uuid4(),
                input="Probe",
                admission=admission,
            )
