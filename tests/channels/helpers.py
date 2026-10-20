"""Build real user-owned flow configurations for channel integration tests."""

from uuid import uuid4

from sqlalchemy.orm import Session

from opsmesh.bootstrap.orchestration import ConfigurationDirectories
from opsmesh.bootstrap.routers import ConfigurationExecutionScopeAdmission
from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.configurations.models import UseConfiguration
from opsmesh.orchestration.configurations.schemas import ConfigurationCreate, ConfigurationSpec
from opsmesh.orchestration.configurations.service import create_configuration
from opsmesh.orchestration.definitions.contracts import FlowDefinitionSpec, FlowGraph, FlowNode
from opsmesh.orchestration.definitions.models import DefinitionRevision
from opsmesh.orchestration.definitions.schemas import DefinitionCreate
from opsmesh.orchestration.definitions.service import create_definition, publish_definition


def flow_configuration(
    session: Session, *, actor: CurrentUser, name: str = "Channel"
) -> tuple[UseConfiguration, DefinitionRevision]:
    definition = create_definition(
        session,
        actor=actor,
        request=DefinitionCreate(
            name=name,
            spec=FlowDefinitionSpec(
                graph=FlowGraph(
                    nodes=[
                        FlowNode(key="wait", kind="wait", wait_kind="signal", signal_name="reply")
                    ]
                )
            ),
        ),
    )
    revision = publish_definition(
        session, actor=actor, definition_id=definition.id, expected_revision=1
    )
    configuration = create_configuration(
        session,
        actor=actor,
        request=ConfigurationCreate(
            name=name, definition_revision_id=revision.id, specification=ConfigurationSpec()
        ),
        resources=ConfigurationDirectories(),
        admission=ConfigurationExecutionScopeAdmission(),
        idempotency_key=str(uuid4()),
    )
    return configuration, revision
