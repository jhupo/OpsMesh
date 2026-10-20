"""Configured hooks retain installed declarations and invited-user authorization."""

from uuid import uuid4

import pytest
from opsmesh_sdk.manifest import HookExport, PackageManifest

from opsmesh.bootstrap.extensions import ExtensionScopes, RunExtensions
from opsmesh.bootstrap.orchestration import ConfigurationDirectories
from opsmesh.bootstrap.routers import ConfigurationExecutionScopeAdmission
from opsmesh.capabilities.models import CapabilityVersion
from opsmesh.extensions.models import ExtensionInstance, Installation
from opsmesh.identity.provisioning import UserIdentityDirectory
from opsmesh.identity.users.service import create_user
from opsmesh.orchestration.configurations.bindings.models import CapabilityBinding
from opsmesh.orchestration.configurations.schemas import (
    ConfigurationCreate,
    ConfigurationHook,
    ConfigurationInvitationCreate,
    ConfigurationSpec,
    ConfigurationUpdate,
)
from opsmesh.orchestration.configurations.service import (
    accept_invitation,
    assemble_configuration,
    create_configuration,
    invite_member,
    update_configuration,
)
from opsmesh.orchestration.contracts import ExecutionScope
from opsmesh.orchestration.definitions.schemas import (
    AgentGraphSpec,
    AgentTemplate,
    DefinitionCreate,
)
from opsmesh.orchestration.definitions.service import create_definition, publish_definition
from opsmesh.orchestration.execution.contracts import ModelConfiguration
from opsmesh.platform.model_connections.service import create_connection, grant_connection
from opsmesh.shared.errors import ForbiddenError
from tests.extensions.test_extensions_postgres import CIPHER
from tests.extensions.test_extensions_postgres import facts as facts


def test_shared_hooks_compile_and_reject_undeclared_stages(database_sessions, facts):
    owner_id, instance_id, installation_id, _, _ = facts
    admission = ConfigurationExecutionScopeAdmission()
    with database_sessions() as session, session.begin():
        actor = UserIdentityDirectory().load_identity(session, owner_id)
        admin_profile = create_user(
            session,
            email=f"{uuid4()}@example.test",
            display_name="Model admin",
            platform_roles=frozenset({"admin"}),
        )
        admin = UserIdentityDirectory().load_identity(session, admin_profile.id)
        connection = create_connection(
            session,
            actor=admin,
            cipher=CIPHER,
            name="model",
            api_key="fixture-only",
            base_url="https://example.test/v1",
            default_model="test",
            models=["test"],
        )
        grant_connection(
            session,
            actor=admin,
            connection_id=connection.id,
            user_id=owner_id,
            models=["test"],
            is_default=True,
        )
        definition = create_definition(
            session,
            actor=actor,
            request=DefinitionCreate(
                name="Hooked analyst",
                kind="agent_graph",
                spec=AgentGraphSpec(
                    entry_agent="main", agents={"main": AgentTemplate(name="Expert")}
                ),
            ),
        )
        published = publish_definition(
            session, actor=actor, definition_id=definition.id, expected_revision=1
        )
        configuration = create_configuration(
            session,
            actor=actor,
            request=ConfigurationCreate(name="Hooked", definition_revision_id=published.id),
            resources=ConfigurationDirectories(),
            admission=admission,
            idempotency_key="hooked",
        )
        installation = session.get(Installation, installation_id)
        manifest = PackageManifest.model_validate(installation.manifest)
        installation.manifest = manifest.model_copy(
            update={
                "hooks": (
                    HookExport(
                        export_id="trace",
                        description="Observe model boundaries",
                        stages=("model.before", "model.after"),
                        failure_policy="observe",
                    ),
                )
            }
        ).model_dump(mode="json")
        version = session.get(CapabilityVersion, installation.version_id)
        version.digest = "a" * 64
        instance = session.get(ExtensionInstance, instance_id)
        instance.configuration_id = configuration.id
        binding = CapabilityBinding(
            owner_user_id=owner_id,
            created_by=owner_id,
            name="trace",
            version_id=version.id,
            version_digest=version.digest,
            configuration={"instance_id": str(instance.id), "export_id": "trace"},
        )
        session.add(binding)
        session.flush()
        settings = ConfigurationSpec(
            models={"default": ModelConfiguration(connection_id=connection.id, name="test")},
            hooks=[
                ConfigurationHook(binding_id=binding.id, stages=["model.before", "model.after"])
            ],
        )
        revision = update_configuration(
            session,
            actor=actor,
            configuration_id=configuration.id,
            request=ConfigurationUpdate(expected_revision=0, specification=settings),
            admission=admission,
        )
        request, frozen = assemble_configuration(
            session,
            actor=actor,
            configuration_id=configuration.id,
            revision=revision,
            session_id=uuid4(),
            input="Analyze logs",
            admission=admission,
        )
        assert len(request.hooks) == 1 and request.hooks[0].failure_policy == "observe"
        assert frozen[str(binding.id)]["instance_revision"] == instance.revision
        guest = create_user(session, email="hook-guest@example.com", display_name="Guest")
        invited = UserIdentityDirectory().load_identity(session, guest.id)
        invitation = invite_member(
            session,
            actor=actor,
            configuration_id=configuration.id,
            request=ConfigurationInvitationCreate(user_id=guest.id, permissions=["read", "use"]),
        )
        accept_invitation(session, actor=invited, invitation_id=invitation.id)
        RunExtensions(ExtensionScopes(admission, CIPHER), CIPHER).validate(
            session,
            principal_id=guest.id,
            scope=ExecutionScope(configuration_id=configuration.id),
            configuration=request.model_dump(mode="json"),
        )
        with pytest.raises(ForbiddenError), session.begin_nested():
            update_configuration(
                session,
                actor=actor,
                configuration_id=configuration.id,
                request=ConfigurationUpdate(
                    expected_revision=1,
                    specification=settings.model_copy(
                        update={
                            "hooks": [
                                ConfigurationHook(binding_id=binding.id, stages=["tool.before"])
                            ]
                        }
                    ),
                ),
                admission=admission,
            )
