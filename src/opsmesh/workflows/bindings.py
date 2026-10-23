"""Authorized graph loading and instance-local references to execution resources."""

from typing import Protocol
from uuid import UUID

from opsmesh_sdk.identity import InvocationSource
from sqlalchemy.orm import Session

from opsmesh.execution.instances.access import get_instance
from opsmesh.execution.sessions.service import get_session
from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.serialization import payload_digest
from opsmesh.users.access import CurrentUser
from opsmesh.users.service import current_user
from opsmesh.workflows import access
from opsmesh.workflows.compiler import compile_spec
from opsmesh.workflows.contracts import (
    CompiledWorkflow,
    PreparedWorkflow,
    ToolBinding,
    ToolNode,
    WorkflowBindings,
    WorkflowPlan,
    WorkflowSpec,
)
from opsmesh.workflows.graph import nested_graphs, validate_graph


class ToolAdapter(Protocol):
    async def authorize(
        self,
        actor: CurrentUser,
        binding: ToolBinding,
        operation: str,
        arguments: dict[str, object],
        source: InvocationSource,
    ) -> None: ...
    async def describe(
        self,
        actor: CurrentUser,
        binding: ToolBinding,
        operation: str,
        *,
        source: InvocationSource | None = None,
    ) -> dict[str, object]: ...
    async def invoke(
        self,
        actor: CurrentUser,
        binding: ToolBinding,
        operation: str,
        arguments: dict[str, object],
        operation_id: UUID,
        descriptor: dict[str, object],
        *,
        source: InvocationSource | None = None,
    ) -> object: ...


def resolve_spec(
    session: Session,
    *,
    actor: CurrentUser,
    spec: WorkflowSpec,
    workflow_id: UUID,
    revision_id: UUID | None = None,
    public: bool = False,
) -> WorkflowPlan:
    actor = current_user(session, actor.user_id)

    def load(identifier: UUID) -> CompiledWorkflow:
        revision, definition = access.load_revision(
            session, actor=actor, revision_id=identifier, use=True
        )
        if public and (revision.visibility != "public" or definition.visibility != "public"):
            raise InvalidArgumentError("Public workflows require public subworkflow revisions")
        child = WorkflowSpec.model_validate(revision.spec)
        return CompiledWorkflow(
            workflow_id=definition.id,
            revision_id=revision.id,
            digest=revision.digest,
            spec=child,
            bounds=validate_graph(child.graph, child.limits),
        )

    return compile_spec(spec, workflow_id=workflow_id, revision_id=revision_id, load=load)


def compile_workflow(session: Session, *, actor: CurrentUser, revision_id: UUID) -> WorkflowPlan:
    revision, definition = access.load_revision(
        session, actor=actor, revision_id=revision_id, use=True
    )
    spec = WorkflowSpec.model_validate(revision.spec)
    if payload_digest(spec.model_dump(mode="json")) != revision.digest:
        raise InvalidArgumentError("The published workflow digest changed")
    return resolve_spec(
        session, actor=actor, spec=spec, workflow_id=definition.id, revision_id=revision.id
    )


def authorize_bindings(session: Session, *, actor: CurrentUser, prepared: PreparedWorkflow) -> None:
    plan, bindings = prepared.plan, prepared.bindings
    actor = current_user(session, actor.user_id)
    for revision_id in (plan.entry.revision_id, *plan.workflows):
        if revision_id is not None:
            access.load_revision(session, actor=actor, revision_id=revision_id, use=True)
    if set(bindings.agents) != set(plan.agent_slots) or set(bindings.tools) != set(plan.tool_slots):
        raise InvalidArgumentError(
            "Bindings must match the workflow's Agent and tool slots exactly"
        )
    for binding in bindings.agents.values():
        instance = get_instance(session, actor=actor, instance_id=binding.instance_id)
        if instance.status != "active":
            raise InvalidArgumentError("A bound Agent instance must be active")
        if binding.session_id:
            conversation = get_session(session, actor=actor, session_id=binding.session_id)
            if conversation.instance_id != instance.id or conversation.status != "active":
                raise InvalidArgumentError(
                    "A bound session must belong to the selected Agent instance"
                )
    graphs = [plan.entry.spec.graph, *(item.spec.graph for item in plan.workflows.values())]
    while graphs:
        for node in graphs.pop().nodes:
            if (
                isinstance(node, ToolNode)
                and node.operation not in bindings.tools[node.binding].allowed_operations
            ):
                raise InvalidArgumentError("A tool binding does not allow its node's operation")
            graphs.extend(body for _, body, _ in nested_graphs(node))


def bind_workflow(
    session: Session, *, actor: CurrentUser, revision_id: UUID, bindings: WorkflowBindings
) -> PreparedWorkflow:
    prepared = PreparedWorkflow(
        plan=compile_workflow(session, actor=actor, revision_id=revision_id), bindings=bindings
    )
    authorize_bindings(session, actor=actor, prepared=prepared)
    return prepared
