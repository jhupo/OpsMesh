from datetime import UTC, datetime

from sqlalchemy import func, select

from backend.app.runs.models import RunEvent
from backend.app.tools.context import ToolContext


class ProductToolEventRecorder:
    def _append_tool_event(self, context: ToolContext, event_type: str, tool_name: str) -> None:
        if context.agent_run_id is None:
            return
        next_sequence = (
            self._session.scalar(
                select(func.coalesce(func.max(RunEvent.sequence), 0)).where(
                    RunEvent.workspace_id == context.workspace_id,
                    RunEvent.agent_run_id == context.agent_run_id,
                )
            )
            or 0
        ) + 1
        self._session.add(
            RunEvent(
                workspace_id=context.workspace_id,
                agent_run_id=context.agent_run_id,
                event_type=event_type,
                sequence=next_sequence,
                message=tool_name,
                created_at=datetime.now(UTC),
            )
        )
