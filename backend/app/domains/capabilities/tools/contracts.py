from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ToolContext:
    workspace_id: UUID
    agent_run_id: UUID | None
    task_id: UUID | None
    allowed_tools: frozenset[str]
    metadata: dict[str, object] | None = None

    def require_tool(self, tool_name: str) -> None:
        if tool_name not in self.allowed_tools:
            raise ToolPermissionError(f"Tool is not allowed: {tool_name}")
    task_step_id: UUID | None = None


class ToolPermissionError(PermissionError):
    pass


class ToolResourceNotFoundError(LookupError):
    pass
