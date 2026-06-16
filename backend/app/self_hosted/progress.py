from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.api.schemas.self_hosted import ProgressEventRequest
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.self_hosted.events import SelfHostedEventRecorder
from backend.app.self_hosted.types import AuthenticatedWorker


class SelfHostedProgressService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._events = SelfHostedEventRecorder(session)

    def upload_progress(
        self,
        auth: AuthenticatedWorker,
        data: ProgressEventRequest,
    ) -> RunEvent:
        run = self._require_worker_run(auth, data.agent_run_id)
        event = self._events.append_run_event(run, data.event_type, data.message, data.metadata)
        self._session.commit()
        self._session.refresh(event)
        return event

    def _require_worker_run(self, auth: AuthenticatedWorker, agent_run_id) -> AgentRun:
        run = self._session.get(AgentRun, agent_run_id)
        if (
            run is None
            or run.workspace_id != auth.worker.workspace_id
            or run.runtime_id != auth.runtime.id
        ):
            raise ValueError("Agent run not found for worker")
        return run
