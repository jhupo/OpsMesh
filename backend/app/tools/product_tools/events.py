

from backend.app.runs.event_writer import RunEventWriter
from backend.app.tools.context import ToolContext


class ProductToolEventRecorder:
    def _append_tool_event(self, context: ToolContext, event_type: str, tool_name: str) -> None:
        if context.agent_run_id is None:
            return
        RunEventWriter(self._session).append(
            workspace_id=context.workspace_id, run_id=context.agent_run_id,
            event_type=event_type, message=tool_name,
        )
