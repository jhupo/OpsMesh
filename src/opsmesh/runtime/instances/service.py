from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.runtime.commands.service import RuntimeCommandService
from opsmesh.runtime.instances import queries
from opsmesh.runtime.instances.models import (
    RuntimeCommand,
    RuntimeEvent,
    RuntimeTemplate,
    WorkspaceRuntime,
)
from opsmesh.shared.config import Settings
from opsmesh.shared.errors import NotFoundError, PolicyDeniedError


class RuntimeEnvironmentService:
    """Manage execution environment configuration; SDK clients own external resources."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def list_templates(self) -> list[RuntimeTemplate]:
        return list(
            self._session.scalars(
                select(RuntimeTemplate)
                .where(RuntimeTemplate.status == "active")
                .order_by(RuntimeTemplate.created_at.desc(), RuntimeTemplate.name)
            )
        )

    def create_runtime(
        self,
        *,
        workspace_id: UUID,
        template_id: UUID,
        name: str,
        runtime_space_id: UUID | None = None,
    ) -> WorkspaceRuntime:
        template = self._session.get(RuntimeTemplate, template_id)
        if template is None or template.status != "active":
            raise NotFoundError("Execution environment template not found")
        if template.image not in self._settings.runtime_allowed_images:
            raise PolicyDeniedError("Sandbox image is not approved by the platform")
        if runtime_space_id is not None:
            from opsmesh.runtime.spaces.models import RuntimeSpace

            space = self._session.scalar(
                select(RuntimeSpace).where(
                    RuntimeSpace.workspace_id == workspace_id,
                    RuntimeSpace.id == runtime_space_id,
                    RuntimeSpace.status == "active",
                )
            )
            if space is None:
                raise NotFoundError("Runtime space not found")
        sandbox_options = {**template.sandbox_options, "image": template.image}
        runtime = WorkspaceRuntime(
            workspace_id=workspace_id,
            runtime_template_id=template.id,
            runtime_space_id=runtime_space_id,
            name=name,
            status="active",
            sandbox_backend_id=template.sandbox_backend_id,
            sandbox_options=sandbox_options,
        )
        self._session.add(runtime)
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

    def list_runtimes(
        self,
        workspace_id: UUID,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[list[WorkspaceRuntime], int]:
        return queries.list_runtimes(
            self._session, workspace_id, limit=limit, offset=offset, status=status
        )

    def get_runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime | None:
        return queries.get_runtime(self._session, workspace_id, runtime_id)

    def set_status(self, workspace_id: UUID, runtime_id: UUID, status: str) -> WorkspaceRuntime:
        if status not in {"active", "disabled", "deleted"}:
            raise ValueError("Invalid execution environment status")
        runtime = self._session.scalar(
            select(WorkspaceRuntime)
            .where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.id == runtime_id,
                WorkspaceRuntime.status != "deleted",
            )
            .with_for_update()
        )
        if runtime is None:
            raise NotFoundError("Execution environment not found")
        runtime.status = status
        if status in {"disabled", "deleted"}:
            from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
            from opsmesh.runtime.temporal.outbox import stage_execution

            stage_execution(
                self._session,
                ExecutionRequest(
                    workspace_id=workspace_id,
                    resource_id=runtime_id,
                    kind=ExecutionKind.SANDBOX_CLEANUP,
                ),
            )
        self._session.commit()
        return runtime

    def queue_command(
        self,
        *,
        workspace_id: UUID,
        runtime_id: UUID,
        command: list[str],
        requested_by_user_id: UUID,
    ) -> RuntimeCommand | None:
        runtime = self.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return None
        if runtime.status != "active":
            raise PolicyDeniedError("Execution environment is disabled")
        return RuntimeCommandService(self._session, self._settings).queue_command(
            workspace_id=workspace_id,
            runtime=runtime,
            command=command,
            requested_by_user_id=requested_by_user_id,
        )

    def list_commands(
        self,
        workspace_id: UUID,
        runtime_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[RuntimeCommand], int]:
        return queries.list_commands(
            self._session, workspace_id, runtime_id, limit=limit, offset=offset
        )

    def list_events(
        self,
        workspace_id: UUID,
        runtime_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[RuntimeEvent], int]:
        return queries.list_events(
            self._session, workspace_id, runtime_id, limit=limit, offset=offset
        )
