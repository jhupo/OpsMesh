from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.runs.event_writer import RunEventWriter
from backend.app.tools.context import ToolContext


class ProductToolContext(Protocol):
    _session: Session

    def _sender_agent_profile_id(self, context: ToolContext) -> UUID: ...


class ProductToolEventRecorder(ProductToolContext):
    def _append_tool_event(self, context: ToolContext, event_type: str, tool_name: str) -> None:
        if context.agent_run_id is None:
            return
        RunEventWriter(self._session).append(
            workspace_id=context.workspace_id, run_id=context.agent_run_id,
            event_type=event_type, message=tool_name,
        )
