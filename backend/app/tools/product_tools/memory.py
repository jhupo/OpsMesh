from uuid import UUID

from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.runs.models import AgentRun
from backend.app.tools.context import ToolContext
from backend.app.tools.errors import ToolResourceNotFoundError
from backend.app.tools.product_tools.events import ProductToolEventRecorder
from backend.app.tools.product_tools.normalization import (
    bounded_optional,
    bounded_text,
    normalized_tags,
)
from backend.app.tools.workspace_memory import WorkspaceMemorySearchService


class WorkspaceMemoryProductTools(ProductToolEventRecorder):
    def search_workspace_memory(
        self,
        context: ToolContext,
        query: str,
        *,
        limit: int = 10,
        source_types: set[str] | None = None,
    ) -> list[dict[str, object]]:
        context.require_tool("search_workspace_memory")
        self._append_tool_event(context, "tool.called", "search_workspace_memory")
        results = WorkspaceMemorySearchService(self._session).search(
            workspace_id=context.workspace_id,
            query=query,
            limit=limit,
            source_types=source_types,
        )
        self._append_tool_event(context, "tool.completed", "search_workspace_memory")
        return results

    def remember_workspace_memory(
        self,
        context: ToolContext,
        *,
        title: str,
        content: str,
        entry_type: str = "note",
        tags: list[str] | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        visibility_scope: str = "workspace",
        importance: int = 0,
        metadata: dict[str, object] | None = None,
    ) -> WorkspaceMemoryEntry:
        context.require_tool("remember_workspace_memory")
        self._append_tool_event(context, "tool.called", "remember_workspace_memory")
        run = self._run_for_context(context)
        entry = WorkspaceMemoryEntry(
            workspace_id=context.workspace_id,
            created_by_agent_profile_id=run.agent_profile_id if run is not None else None,
            created_by_agent_run_id=context.agent_run_id,
            source_type=bounded_optional(source_type, 80),
            source_id=bounded_optional(source_id, 120),
            entry_type=bounded_text(entry_type, 80, "note"),
            title=bounded_text(title, 240, "Untitled memory"),
            content=content.strip(),
            tags=normalized_tags(tags),
            visibility_scope=bounded_text(visibility_scope, 32, "workspace"),
            importance=max(0, min(100, importance)),
            status="active",
            memory_metadata=metadata or {},
        )
        self._session.add(entry)
        self._session.flush()
        self._append_tool_event(context, "tool.completed", "remember_workspace_memory")
        return entry

    def archive_workspace_memory(
        self,
        context: ToolContext,
        memory_entry_id: UUID,
    ) -> WorkspaceMemoryEntry:
        context.require_tool("archive_workspace_memory")
        self._append_tool_event(context, "tool.called", "archive_workspace_memory")
        entry = self._session.get(WorkspaceMemoryEntry, memory_entry_id)
        if entry is None or entry.workspace_id != context.workspace_id:
            raise ToolResourceNotFoundError("Workspace memory entry not found")
        entry.status = "archived"
        self._session.flush([entry])
        self._append_tool_event(context, "tool.completed", "archive_workspace_memory")
        return entry

    def _run_for_context(self, context: ToolContext) -> AgentRun | None:
        if context.agent_run_id is None:
            return None
        run = self._session.get(AgentRun, context.agent_run_id)
        if run is None or run.workspace_id != context.workspace_id:
            return None
        return run
