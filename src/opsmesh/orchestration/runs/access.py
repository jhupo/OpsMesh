"""Composition-owned state and explicit execution operations."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.access import authorize_scope
from opsmesh.orchestration.contracts import ExecutionScope, ExecutionSubject, ScopeAdmission
from opsmesh.orchestration.runs.models import FlowRun, Run, StepAttempt
from opsmesh.orchestration.sessions.access import load_visible_session
from opsmesh.orchestration.sessions.models import SessionRecord
from opsmesh.shared.errors import ForbiddenError, NotFoundError


def load_flow_run(
    session: Session,
    *,
    actor: CurrentUser,
    run_id: UUID,
    write: bool = False,
    lock: bool = False,
    allow_archived: bool = False,
) -> FlowRun:
    record = session.get(FlowRun, run_id)
    if record is None:
        raise NotFoundError("Flow run not found")
    authorize_scope(
        session,
        actor=actor,
        owner_id=record.principal_id,
        configuration_id=record.configuration_id,
        write=write,
        allow_archived=allow_archived,
    )
    if lock:
        session.get(FlowRun, record.root_id, with_for_update=True, populate_existing=True)
        record = session.get(FlowRun, run_id, with_for_update=True, populate_existing=True)
        assert record is not None
    return record


@dataclass(frozen=True)
class OwnedAgentCancellation:
    """Current flow control authorizes cancellation of one stored child only."""

    flow_id: UUID
    step_id: UUID
    actor: CurrentUser

    def admit(self, session: Session, *, subject: ExecutionSubject, scope: ExecutionScope) -> None:
        flow = load_flow_run(
            session, actor=self.actor, run_id=self.flow_id, write=True, allow_archived=True
        )
        step = session.get(StepAttempt, self.step_id)
        child = session.get(Run, step.child_id) if step and step.child_id else None
        if (
            step is None
            or step.run_id != flow.id
            or step.child_kind != "agent"
            or child is None
            or child.principal_id != subject.user_id
            or child.principal_id != flow.principal_id
            or scope.configuration_id != child.scope_id
            or child.owner_ref != f"configuration:{child.scope_id}"
        ):
            raise ForbiddenError("Flow cancellation does not own this Agent child")


def load_run(
    session: Session,
    *,
    run_id: UUID,
    subject: ExecutionSubject,
    admission: ScopeAdmission,
    lock: bool = False,
) -> Run:
    record = session.get(Run, run_id)
    if record is None or record.principal_id != subject.user_id:
        raise NotFoundError("The Run does not exist")
    load_visible_session(
        session, session_id=record.session_id, subject=subject, admission=admission, lock=lock
    )
    if lock:
        record = session.scalars(select(Run).where(Run.id == run_id).with_for_update()).one()
    return record


def lock_run_facts(session: Session, run_id: UUID) -> Run:
    """Worker effect completion must remain possible after access is revoked."""
    record = session.get(Run, run_id)
    if record is None:
        raise NotFoundError("The execution Run does not exist")
    session.scalars(
        select(SessionRecord).where(SessionRecord.id == record.session_id).with_for_update()
    ).one()
    return session.scalars(select(Run).where(Run.id == run_id).with_for_update()).one()
