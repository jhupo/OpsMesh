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
from backend.app.tools.workspace_memory_documents import memory_entry_source_type


class WorkspaceMemoryProductTools(ProductToolEventRecorder):
    def search_workspace_memory(
        self,
        context: ToolContext,
        query: str,
        *,
        limit: int = 10,
        source_types: set[str] | None = None,
        allowed_source_types: set[str] | None = None,
        allowed_tags: set[str] | None = None,
    ) -> list[dict[str, object]]:
        context.require_tool("search_workspace_memory")
        self._append_tool_event(context, "tool.called", "search_workspace_memory")
        results = WorkspaceMemorySearchService(self._session).search(
            workspace_id=context.workspace_id,
            query=query,
            limit=limit,
            source_types=_intersect_optional(source_types, allowed_source_types),
            tags=allowed_tags,
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
        allowed_source_types: set[str] | None = None,
        allowed_tags: set[str] | None = None,
    ) -> WorkspaceMemoryEntry:
        context.require_tool("remember_workspace_memory")
        self._append_tool_event(context, "tool.called", "remember_workspace_memory")
        run = self._run_for_context(context)
        normalized_entry_tags = normalized_tags(tags)
        normalized_entry_type = bounded_text(entry_type, 80, "note")
        normalized_source_type = bounded_optional(source_type, 80)
        authorized_source_type = (
            normalized_source_type
            if normalized_entry_type == "indexed_chunk" and normalized_source_type
            else "workspace_memory"
        )
        if (
            allowed_source_types is not None
            and authorized_source_type not in allowed_source_types
        ):
            raise ValueError("Memory source type is outside the authorized resource scope")
        if allowed_tags is not None and not allowed_tags.intersection(normalized_entry_tags):
            raise ValueError("Memory tags are outside the authorized resource scope")
        entry = WorkspaceMemoryEntry(
            workspace_id=context.workspace_id,
            created_by_agent_profile_id=run.agent_profile_id if run is not None else None,
            created_by_agent_run_id=context.agent_run_id,
            source_type=normalized_source_type,
            source_id=bounded_optional(source_id, 120),
            entry_type=normalized_entry_type,
            title=bounded_text(title, 240, "Untitled memory"),
            content=content.strip(),
            tags=normalized_entry_tags,
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
        *,
        allowed_source_types: set[str] | None = None,
        allowed_tags: set[str] | None = None,
    ) -> WorkspaceMemoryEntry:
        context.require_tool("archive_workspace_memory")
        self._append_tool_event(context, "tool.called", "archive_workspace_memory")
        entry = self._session.get(WorkspaceMemoryEntry, memory_entry_id)
        if entry is None or entry.workspace_id != context.workspace_id:
            raise ToolResourceNotFoundError("Workspace memory entry not found")
        if (
            allowed_source_types is not None
            and memory_entry_source_type(entry) not in allowed_source_types
        ):
            raise ToolResourceNotFoundError("Memory entry is outside the authorized resource scope")
        if allowed_tags is not None and not allowed_tags.intersection(entry.tags):
            raise ToolResourceNotFoundError("Memory entry is outside the authorized resource scope")
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


def _intersect_optional(
    requested: set[str] | None,
    allowed: set[str] | None,
) -> set[str] | None:
    if allowed is None:
        return requested
    if requested is None:
        return allowed
    return requested & allowed
