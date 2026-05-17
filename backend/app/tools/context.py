from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ToolContext:
    workspace_id: UUID
    agent_run_id: UUID | None
    task_id: UUID | None
    allowed_tools: frozenset[str]

    def require_tool(self, tool_name: str) -> None:
        from backend.app.tools.errors import ToolPermissionError

        if tool_name not in self.allowed_tools:
            raise ToolPermissionError(f"Tool is not allowed: {tool_name}")

