"""Native Session I/O runs in Activities, with product authorization and receipts."""

from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity

from opsmesh.orchestration.contracts import ExecutionSubject, ScopeAdmission
from opsmesh.orchestration.sessions import service
from opsmesh.orchestration.sessions.contracts import (
    HistoryOperation,
    SessionActivityRequest,
    SessionRunActivity,
    SessionWriter,
)
from opsmesh.orchestration.sessions.history import HistoryGateway


class SessionActivities:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        gateway: HistoryGateway,
        admission: ScopeAdmission,
        run_activity: SessionRunActivity,
        writer: SessionWriter,
    ) -> None:
        self.sessions = sessions
        self.gateway = gateway
        self.admission = admission
        self.run_activity = run_activity
        self.writer = writer

    @activity.defn(name="orchestration.session")
    async def operate(self, request: SessionActivityRequest) -> Any:
        subject = ExecutionSubject(request.principal_id)
        if request.method == "get_items":
            with self.sessions() as session:
                self.writer.authorize_writer(
                    session,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    principal_id=request.principal_id,
                )
                return await service.read_history(
                    session,
                    self.gateway,
                    subject=subject,
                    admission=self.admission,
                    session_id=request.session_id,
                    limit=request.limit,
                    writer=self.writer,
                    writer_run_id=request.run_id,
                )
        info = activity.info()
        result = await service.operate_history(
            self.sessions,
            self.gateway,
            subject=subject,
            admission=self.admission,
            activity=self.run_activity,
            session_id=request.session_id,
            operation=HistoryOperation(
                method=request.method, items=tuple(request.items), limit=request.limit
            ),
            operation_id=f"{info.workflow_id}:{info.activity_id}",
            writer=self.writer,
            writer_run_id=request.run_id,
        )
        return result.item if request.method == "pop_item" else None
