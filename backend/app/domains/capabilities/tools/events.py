from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.domains.capabilities.tools.contracts import ToolContext
from backend.app.domains.orchestration.runs.events import RunEventWriter


class ProductToolEventRecorder:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, context: ToolContext, event_type: str, tool_name: str) -> None:
        if context.agent_run_id is None:
            return
        RunEventWriter(self._session).append(
            workspace_id=context.workspace_id, run_id=context.agent_run_id,
            event_type=event_type, message=tool_name,
        )
