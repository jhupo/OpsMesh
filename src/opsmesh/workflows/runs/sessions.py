"""Trusted ingress scopes isolate bound Agents' native conversation storage."""

from uuid import UUID

from opsmesh_sdk.identity import InvocationSource
from sqlalchemy import text
from sqlalchemy.orm import Session

from opsmesh.execution.sessions.service import create_session, get_session
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.serialization import payload_digest
from opsmesh.users.access import CurrentUser
from opsmesh.workflows.contracts import AgentBinding
from opsmesh.workflows.runs.models import WorkflowAgentSession


def conversation_session(
    session: Session,
    *,
    actor: CurrentUser,
    source: InvocationSource,
    workflow_instance_id: UUID,
    binding_name: str,
    binding: AgentBinding,
) -> UUID | None:
    if source.session_scope is None:
        return binding.session_id
    scope = payload_digest(
        {
            "ingress": source.session_scope,
            "workflow": str(workflow_instance_id),
            "user": str(actor.user_id),
            "agent": str(binding.instance_id),
        }
    )
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": "workflow-session:" + scope},
    )
    row = session.get(WorkflowAgentSession, (scope, binding_name), with_for_update=True)
    if row is None:
        native = create_session(session, actor=actor, instance_id=binding.instance_id)
        row = WorkflowAgentSession(
            scope_key=scope,
            binding=binding_name,
            instance_id=binding.instance_id,
            user_id=actor.user_id,
            session_id=native.id,
        )
        session.add(row)
    elif row.user_id != actor.user_id or row.instance_id != binding.instance_id:
        raise ConflictError("The workflow conversation belongs to another binding")
    native = get_session(session, actor=actor, session_id=row.session_id)
    if native.status != "active":
        raise ConflictError("The workflow conversation is not active")
    return row.session_id
