from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.artifacts.models import Artifact
from backend.app.files.models import WorkspaceFile
from backend.app.files.security import safe_filename
from backend.app.runs.models import RunEvent
from backend.app.tools.context import ToolContext
from backend.app.tools.errors import ToolResourceNotFoundError


class ProductToolService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_workspace_files(self, context: ToolContext) -> list[WorkspaceFile]:
        context.require_tool("list_workspace_files")
        self._append_tool_event(context, "tool.called", "list_workspace_files")
        files = list(
            self._session.scalars(
                select(WorkspaceFile)
                .where(WorkspaceFile.workspace_id == context.workspace_id)
                .order_by(WorkspaceFile.created_at.desc())
            ).all()
        )
        self._append_tool_event(context, "tool.completed", "list_workspace_files")
        return files

    def read_workspace_file(self, context: ToolContext, file_id: UUID) -> WorkspaceFile:
        context.require_tool("read_workspace_file")
        self._append_tool_event(context, "tool.called", "read_workspace_file")
        file = self._session.get(WorkspaceFile, file_id)
        if file is None or file.workspace_id != context.workspace_id:
            raise ToolResourceNotFoundError("Workspace file not found")
        self._append_tool_event(context, "tool.completed", "read_workspace_file")
        return file

    def write_artifact(
        self,
        context: ToolContext,
        *,
        filename: str,
        content: bytes,
        content_type: str,
        artifact_type: str = "file",
    ) -> Artifact:
        context.require_tool("write_artifact")
        self._append_tool_event(context, "tool.called", "write_artifact")
        checksum = sha256(content).hexdigest()
        sanitized_filename = safe_filename(filename, default="artifact.bin")
        artifact = Artifact(
            workspace_id=context.workspace_id,
            task_id=context.task_id,
            agent_run_id=context.agent_run_id,
            artifact_type=artifact_type,
            filename=sanitized_filename,
            content_type=content_type,
            size_bytes=len(content),
            checksum_sha256=checksum,
            storage_key=f"workspaces/{context.workspace_id}/artifacts/{checksum}/{sanitized_filename}",
            created_at=datetime.now(UTC),
        )
        self._session.add(artifact)
        self._append_tool_event(context, "tool.completed", "write_artifact")
        self._session.flush()
        return artifact

    def search_workspace_memory(self, context: ToolContext, query: str) -> list[dict[str, object]]:
        context.require_tool("search_workspace_memory")
        self._append_tool_event(context, "tool.called", "search_workspace_memory")
        self._append_tool_event(context, "tool.completed", "search_workspace_memory")
        return []

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
