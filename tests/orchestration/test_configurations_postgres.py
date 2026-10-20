"""Composition ownership and invitation revocation use real PostgreSQL facts."""

from uuid import uuid4

import pytest

from opsmesh.bootstrap.orchestration import (
    ConfigurationCleanupAuthorization,
    ConfigurationDirectories,
    ExecutionAssets,
    ExecutionModelConnections,
)
from opsmesh.bootstrap.routers import (
    ConfigurationExecutionScopeAdmission,
    ConfigurationScopeAdmission,
)
from opsmesh.identity.provisioning import UserIdentityDirectory
from opsmesh.identity.users.service import create_user
from opsmesh.orchestration.configurations.closing import drain_configuration
from opsmesh.orchestration.configurations.models import (
    ConfigurationClosure,
    ConfigurationMember,
    ConfigurationRevision,
)
from opsmesh.orchestration.configurations.schemas import (
    ConfigurationCreate,
    ConfigurationInvitationCreate,
    ConfigurationRunCreate,
    ConfigurationSpec,
    ConfigurationUpdate,
)
from opsmesh.orchestration.configurations.service import (
    accept_invitation,
    authorize_configuration_session,
    create_configuration,
    default_configuration_session,
    invite_member,
    submit_configuration_run,
    update_configuration,
)
from opsmesh.orchestration.contracts import ExecutionScope, ExecutionSubject
from opsmesh.orchestration.definitions.schemas import (
    AgentGraphSpec,
    AgentTemplate,
    DefinitionCreate,
)
from opsmesh.orchestration.definitions.service import create_definition, publish_definition
from opsmesh.orchestration.execution.contracts import ModelConfiguration
from opsmesh.orchestration.runs.scheduling import is_session_head
from opsmesh.orchestration.runtime.access import load_environment
from opsmesh.orchestration.runtime.models import Environment
from opsmesh.platform.model_connections.service import create_connection, grant_connection
from opsmesh.shared.errors import ForbiddenError, NotFoundError
from tests.conftest import dispatch_cipher


def test_invitation_preserves_private_sessions_and_revocation(database_sessions, tmp_path):
    admission = ConfigurationExecutionScopeAdmission()
    cipher = dispatch_cipher()
    with database_sessions() as session, session.begin():
        owner = create_user(
            session,
            email=f"owner-{uuid4()}@example.com",
            display_name="Owner",
            platform_roles=frozenset({"admin"}),
        )
        guest = create_user(session, email=f"guest-{uuid4()}@example.com", display_name="Guest")
        users = UserIdentityDirectory()
        actor, invited = (
            users.load_identity(session, owner.id),
            users.load_identity(session, guest.id),
        )
        connection = create_connection(
            session,
            actor=actor,
            cipher=cipher,
            name="model",
            api_key="platform-test-key",
            base_url="https://example.test/v1",
            default_model="test",
            models=["test"],
        )
        grant_connection(
            session, actor=actor, connection_id=connection.id, user_id=owner.id, models=["test"]
        )
        definition = create_definition(
            session,
            actor=actor,
            request=DefinitionCreate(
                name="User expert",
                kind="agent_graph",
                spec=AgentGraphSpec(
                    entry_agent="main", agents={"main": AgentTemplate(name="Expert")}
                ),
            ),
        )
        version = publish_definition(
            session, actor=actor, definition_id=definition.id, expected_revision=1
        )
        configuration = create_configuration(
            session,
            actor=actor,
            request=ConfigurationCreate(
                name="Shared",
                definition_revision_id=version.id,
                specification=ConfigurationSpec(
                    models={"default": ModelConfiguration(connection_id=connection.id, name="test")}
                ),
            ),
            resources=ConfigurationDirectories(),
            admission=admission,
            idempotency_key="configuration",
        )
        frozen = (
            session.query(ConfigurationRevision).filter_by(configuration_id=configuration.id).one()
        )
        assert frozen.frozen_bindings[f"model:{connection.id}"]["revision"] == 1
        private = default_configuration_session(
            session, actor=actor, configuration_id=configuration.id, admission=admission
        )
        invitation = invite_member(
            session,
            actor=actor,
            configuration_id=configuration.id,
            request=ConfigurationInvitationCreate(
                user_id=guest.id,
                permissions=["read", "use"],
                directory_access=True,
            ),
        )
        accept_invitation(session, actor=invited, invitation_id=invitation.id)
        guest_session = default_configuration_session(
            session, actor=invited, configuration_id=configuration.id, admission=admission
        )
        repeated = default_configuration_session(
            session, actor=invited, configuration_id=configuration.id, admission=admission
        )
        assert repeated.id == guest_session.id
        assert private.id != guest_session.id
        assert guest_session.principal_id == guest.id
        models = ExecutionModelConnections(cipher, {"test": 4096})
        with pytest.raises(ForbiddenError):
            models.resolve(
                session,
                subject=ExecutionSubject(guest.id),
                scope=ExecutionScope(configuration_id=configuration.id),
                connection_id=connection.id,
                model="test",
            )
        grant_connection(
            session, actor=actor, connection_id=connection.id, user_id=guest.id, models=["test"]
        )
        shared_model = models.resolve(
            session,
            subject=ExecutionSubject(guest.id),
            scope=ExecutionScope(configuration_id=configuration.id),
            connection_id=connection.id,
            model="test",
        )
        assert shared_model.api_key == "platform-test-key"
        environment = Environment(
            scope_id=configuration.id,
            principal_id=owner.id,
            owner_ref=f"configuration:{configuration.id}",
            status="ready",
            configuration={"image": "test-image"},
        )
        session.add(environment)
        session.flush()
        update_configuration(
            session,
            actor=actor,
            configuration_id=configuration.id,
            request=ConfigurationUpdate(
                expected_revision=1,
                specification=ConfigurationSpec(
                    models={
                        "default": ModelConfiguration(connection_id=connection.id, name="test")
                    },
                    environment_id=environment.id,
                ),
            ),
            admission=admission,
        )
        with pytest.raises(NotFoundError):
            load_environment(
                session,
                environment_id=environment.id,
                subject=ExecutionSubject(guest.id),
                admission=admission,
            )
        assert (
            load_environment(
                session,
                environment_id=environment.id,
                subject=ExecutionSubject(guest.id),
                admission=admission,
                shared_use=True,
            ).id
            == environment.id
        )
        assets = ExecutionAssets(tmp_path, ConfigurationScopeAdmission())
        submitted = []
        for user, key in ((invited, "guest-one"), (invited, "guest-two"), (actor, "owner-one")):
            submitted.append(
                submit_configuration_run(
                    session,
                    actor=user,
                    configuration_id=configuration.id,
                    request=ConfigurationRunCreate(configuration_revision=2, input=key),
                    idempotency_key=key,
                    admission=admission,
                    cipher=cipher,
                    assets=assets,
                )
            )
        first, second, independent = submitted
        assert first.principal_id == second.principal_id == guest.id
        assert first.session_id == second.session_id == guest_session.id
        assert independent.principal_id == owner.id and independent.session_id == private.id
        assert is_session_head(session, first)
        assert not is_session_head(session, second)
        assert is_session_head(session, independent)
        first.status = "succeeded"
        session.flush()
        assert is_session_head(session, second)
        assert (
            models.resolve(
                session,
                subject=ExecutionSubject(guest.id),
                scope=ExecutionScope(),
                connection_id=connection.id,
                model="test",
            ).api_key
            == "platform-test-key"
        )
        with pytest.raises(NotFoundError):
            authorize_configuration_session(
                session,
                actor=actor,
                configuration_id=configuration.id,
                session_id=guest_session.id,
            )
        assert (
            authorize_configuration_session(
                session,
                actor=invited,
                configuration_id=configuration.id,
                session_id=guest_session.id,
                permission="use",
            ).id
            == configuration.id
        )
        with pytest.raises(NotFoundError):
            authorize_configuration_session(
                session, actor=invited, configuration_id=configuration.id, session_id=private.id
            )
        member = (
            session.query(ConfigurationMember)
            .filter_by(configuration_id=configuration.id, user_id=guest.id)
            .one()
        )
        session.delete(member)
        session.flush()
        # Configuration revocation does not revoke a separate administrator model grant.
        assert (
            models.resolve(
                session,
                subject=ExecutionSubject(guest.id),
                scope=ExecutionScope(configuration_id=configuration.id),
                connection_id=connection.id,
                model="test",
            ).api_key
            == "platform-test-key"
        )
        with pytest.raises(NotFoundError):
            authorize_configuration_session(
                session,
                actor=invited,
                configuration_id=configuration.id,
                session_id=guest_session.id,
            )
        configuration.status = "draining"
        closure = ConfigurationClosure(configuration_id=configuration.id, requested_by_id=owner.id)
        session.add(closure)
        session.flush()
        pending, uncertain = drain_configuration(
            session,
            configuration_id=configuration.id,
            cleanup_id=closure.id,
            authorization=ConfigurationCleanupAuthorization(),
            cipher=cipher,
        )
        assert pending == 3 and uncertain == 0
        assert second.status == independent.status == "cancelling"
        assert first.status == "succeeded"
