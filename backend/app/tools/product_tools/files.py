from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.artifacts.models import Artifact
from backend.app.artifacts.persistence import ArtifactPersistenceError, ArtifactPersistenceService
from backend.app.files.content import (
    WorkspaceFileContent,
    WorkspaceFileContentReader,
    WorkspaceFileReadError,
)
from backend.app.files.models import WorkspaceFile
from backend.app.files.runtime_policy import runtime_file_denial_code
from backend.app.files.security import safe_filename
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import TaskStep
from backend.app.tools.context import ToolContext
from backend.app.tools.errors import ToolResourceNotFoundError
from backend.app.tools.product_tools.events import ProductToolEventRecorder


class WorkspaceFileProductTools(ProductToolEventRecorder):
    _workspace_file_content_reader: WorkspaceFileContentReader | None
    _artifact_persistence: ArtifactPersistenceService | None

    def list_workspace_files(
        self,
        context: ToolContext,
        *,
        allowed_file_ids: set[UUID] | None = None,
    ) -> list[WorkspaceFile]:
        context.require_tool("list_workspace_files")
        self._append_tool_event(context, "tool.called", "list_workspace_files")
        statement = select(WorkspaceFile).where(
            WorkspaceFile.workspace_id == context.workspace_id,
            WorkspaceFile.status == "active",
        )
        if allowed_file_ids is not None:
            statement = statement.where(WorkspaceFile.id.in_(allowed_file_ids))
        candidates = list(
            self._session.scalars(statement.order_by(WorkspaceFile.created_at.desc())).all()
        )
        files = [file for file in candidates if _visible_to_agent_runtime(file)]
        self._append_tool_event(context, "tool.completed", "list_workspace_files")
        return files

    def read_workspace_file(
        self,
        context: ToolContext,
        file_id: UUID,
        *,
        allowed_file_ids: set[UUID] | None = None,
    ) -> WorkspaceFileContent:
        context.require_tool("read_workspace_file")
        self._append_tool_event(context, "tool.called", "read_workspace_file")
        file = self.resolve_workspace_file(
            context,
            file_id,
            allowed_file_ids=allowed_file_ids,
        )
        reader = self._workspace_file_content_reader
        if reader is None:
            raise WorkspaceFileReadError(
                "workspace_file_storage_unavailable",
                "Workspace file storage is not configured for agent tools",
            )
        content = reader.read(file, workspace_id=context.workspace_id)
        self._append_tool_event(context, "tool.completed", "read_workspace_file")
        return content

    def resolve_workspace_file(
        self,
        context: ToolContext,
        file_id: UUID,
        *,
        allowed_file_ids: set[UUID] | None = None,
    ) -> WorkspaceFile:
        context.require_tool("read_workspace_file")
        file = self._session.get(WorkspaceFile, file_id)
        if (
            file is None
            or file.workspace_id != context.workspace_id
            or file.status != "active"
        ):
            raise ToolResourceNotFoundError("Workspace file not found")
        if allowed_file_ids is not None and file.id not in allowed_file_ids:
            raise ToolResourceNotFoundError("Workspace file not found in authorized resource scope")
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
        persistence = self._artifact_persistence
        if persistence is None:
            raise ArtifactPersistenceError(
                "artifact_storage_unavailable",
                "Artifact storage is not configured",
            )
        self._append_tool_event(context, "tool.called", "write_artifact")
        checksum = sha256(content).hexdigest()
        sanitized_filename = safe_filename(filename, default="artifact.bin")
        binding = self._artifact_binding(context)
        artifact_id = uuid4()
        artifact = Artifact(
            id=artifact_id,
            workspace_id=context.workspace_id,
            task_id=context.task_id,
            agent_run_id=context.agent_run_id,
            task_step_id=binding["task_step_id"],
            agent_profile_id=binding["agent_profile_id"],
            work_package_id=binding["work_package_id"],
            version=binding["version"],
            supersedes_artifact_id=binding["supersedes_artifact_id"],
            review_status="pending",
            artifact_type=artifact_type,
            filename=sanitized_filename,
            content_type=content_type,
            size_bytes=len(content),
            checksum_sha256=checksum,
            storage_key=(
                f"workspaces/{context.workspace_id}/artifacts/{artifact_id}/"
                f"{sanitized_filename}"
            ),
            created_at=datetime.now(UTC),
        )
        self._append_tool_event(context, "tool.completed", "write_artifact")
        return persistence.persist_new(artifact, content)

    def _artifact_binding(self, context: ToolContext) -> dict[str, object]:
        run = (
            self._session.get(AgentRun, context.agent_run_id)
            if context.agent_run_id is not None
            else None
        )
        step = (
            self._session.get(TaskStep, run.task_step_id)
            if run is not None and run.task_step_id is not None
            else None
        )
        if step is not None and step.workspace_id != context.workspace_id:
            step = None
        work_package_id = step.work_package_id if step is not None else None
        previous = self._latest_artifact_for_work_package(context, work_package_id)
        return {
            "task_step_id": step.id if step is not None else None,
            "agent_profile_id": run.agent_profile_id if run is not None else None,
            "work_package_id": work_package_id,
            "version": (previous.version + 1) if previous is not None else 1,
            "supersedes_artifact_id": previous.id if previous is not None else None,
        }

    def _latest_artifact_for_work_package(
        self,
        context: ToolContext,
        work_package_id: str | None,
    ) -> Artifact | None:
        if context.task_id is None or work_package_id is None:
            return None
        return self._session.scalar(
            select(Artifact)
            .where(
                Artifact.workspace_id == context.workspace_id,
                Artifact.task_id == context.task_id,
                Artifact.work_package_id == work_package_id,
            )
            .order_by(Artifact.version.desc(), Artifact.created_at.desc())
        )


def _visible_to_agent_runtime(file: WorkspaceFile) -> bool:
    try:
        return runtime_file_denial_code(file) is None
    except ValueError:
        return False
