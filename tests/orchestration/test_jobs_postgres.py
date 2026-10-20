"""Host delegation uses real Invocation and FlowRun facts, without a second job table."""

import pytest
from opsmesh_sdk.manifest import PackageManifest
from sqlalchemy import event, func, select

from opsmesh.bootstrap.orchestration import ConfigurationDirectories, host_jobs
from opsmesh.bootstrap.routers import ConfigurationExecutionScopeAdmission
from opsmesh.extensions.events import publish_event
from opsmesh.extensions.models import ExtensionInstance, Installation, Invocation
from opsmesh.orchestration.configurations.schemas import ConfigurationCreate, ConfigurationSpec
from opsmesh.orchestration.configurations.service import create_configuration
from opsmesh.orchestration.definitions.contracts import (
    FlowCondition,
    FlowDefinitionSpec,
    FlowGraph,
    FlowNode,
)
from opsmesh.orchestration.definitions.schemas import DefinitionCreate
from opsmesh.orchestration.definitions.service import create_definition, publish_definition
from opsmesh.orchestration.runs.models import FlowRun
from tests.extensions.test_extensions_postgres import facts as facts
from tests.orchestration.test_flows_postgres import flow_facts as flow_facts


def test_authenticated_job_is_idempotent(database_sessions, facts, flow_facts):  # noqa: F811
    runtime = flow_facts[0]
    user_id, instance_id, installation_id, _, invocation_id = facts
    with database_sessions() as session, session.begin():
        actor = runtime.users.load_identity(session, user_id)
        instance = session.get(ExtensionInstance, instance_id)
        installation = session.get(Installation, installation_id)
        invocation = session.get(Invocation, invocation_id)
        instance.permissions = [*instance.permissions, "host.jobs"]
        installation.permission_ceiling = [*installation.permission_ceiling, "host.jobs"]
        invocation.snapshot = {**invocation.snapshot, "permissions": [*instance.permissions]}
        definition = create_definition(
            session,
            actor=actor,
            request=DefinitionCreate(
                name="Job target",
                spec=FlowDefinitionSpec(
                    graph=FlowGraph(
                        nodes=[
                            FlowNode(
                                key="yes",
                                kind="condition",
                                condition=FlowCondition(operator="eq", left=1, right=1),
                            )
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
                name="Job", definition_revision_id=revision.id, specification=ConfigurationSpec()
            ),
            resources=ConfigurationDirectories(),
            admission=ConfigurationExecutionScopeAdmission(),
            idempotency_key="job-config",
        )
        instance.configuration_id = configuration.id
        invocation.snapshot = {**invocation.snapshot, "configuration_id": str(configuration.id)}
        revision_id = str(revision.id)
    request = {
        "method": "POST",
        "path": "/host/jobs",
        "body": {
            "definition_revision": revision_id,
            "entry_step": "flow",
            "input": {},
            "idempotency_key": "same-job",
        },
    }
    first = host_jobs(runtime, invocation_id=invocation_id, operation_id="rpc:1", request=request)
    second = host_jobs(runtime, invocation_id=invocation_id, operation_id="rpc:1", request=request)
    assert first == second and first["status"] == 202
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(FlowRun)) == 1


def test_source_event_outbox_commits_and_rolls_back_with_fact(database_sessions, facts):  # noqa: F811
    from opsmesh.bootstrap.source_notifications import publish_source
    from opsmesh.extensions.models import ExtensionEvent
    from opsmesh.temporal.dispatch.models import TemporalOutboxEntry

    _, _, installation_id, _, invocation_id = facts
    already_registered = event.contains(ExtensionEvent, "after_insert", publish_source)
    if not already_registered:
        event.listen(ExtensionEvent, "after_insert", publish_source)
    try:

        def publish(session):
            installation = session.get(Installation, installation_id)
            manifest = PackageManifest.model_validate(installation.manifest).model_copy(
                update={"published_events": ("changed",)}
            )
            return publish_event(
                session,
                invocation=session.get(Invocation, invocation_id),
                manifest=manifest,
                operation_id="source-once",
                event_type="changed",
                payload={"value": 1},
            )

        # Fixture creation also publishes durable platform audit events.
        with database_sessions() as session:
            initial_dispatches = session.scalar(
                select(func.count()).select_from(TemporalOutboxEntry)
            )
        with pytest.raises(RuntimeError), database_sessions() as session, session.begin():
            publish(session)
            raise RuntimeError("Abort the event publication")
        with database_sessions() as session:
            assert session.scalar(select(func.count()).select_from(ExtensionEvent)) == 0
            assert (
                session.scalar(select(func.count()).select_from(TemporalOutboxEntry))
                == initial_dispatches
            )
        with database_sessions() as session, session.begin():
            first = publish(session)
            assert publish(session).id == first.id
        with database_sessions() as session:
            assert session.scalar(select(func.count()).select_from(ExtensionEvent)) == 1
            assert (
                session.scalar(select(func.count()).select_from(TemporalOutboxEntry))
                == initial_dispatches + 1
            )
    finally:
        if not already_registered:
            event.remove(ExtensionEvent, "after_insert", publish_source)
