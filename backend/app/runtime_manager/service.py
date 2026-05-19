from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.admin.policies import PlatformPolicyService
from backend.app.core.config import Settings
from backend.app.runtime_manager.contracts import DockerRuntimeClient, RuntimeLimits
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtime_manager.safety import RuntimeSafetyPolicy
from backend.app.runtime_spaces.service import RuntimeSpaceService
from backend.app.runtimes.models import (
    RuntimeCommand,
    RuntimeEvent,
    RuntimeTemplate,
    WorkspaceRuntime,
)


class RuntimeControlService:
    def __init__(
        self,
        session: Session,
        docker_client: DockerRuntimeClient,
        settings: Settings,
    ) -> None:
        self._session = session
        self._manager = RuntimeManager(session, docker_client)
        self._safety = RuntimeSafetyPolicy(
            tuple(settings.runtime_allowed_images),
            PlatformPolicyService(session).risky_execution_policy(),
        )

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
        limits: RuntimeLimits | None,
        network_disabled: bool,
        runtime_space_id: UUID | None = None,
    ) -> WorkspaceRuntime | None:
        template = self._session.get(RuntimeTemplate, template_id)
        if template is None:
            return None
        if runtime_space_id is not None:
            RuntimeSpaceService(self._session).require_runtime_space(
                workspace_id,
                runtime_space_id,
            )
        self._safety.assert_template_allowed(template)
        self._safety.assert_network_allowed(template, network_disabled=network_disabled)
        return self._manager.create_runtime(
            workspace_id=workspace_id,
            template=template,
            name=name,
            limits=limits or self._limits_from_template(template),
            runtime_space_id=runtime_space_id,
            network_disabled=network_disabled,
        )

    def list_runtimes(
        self,
        workspace_id: UUID,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[list[WorkspaceRuntime], int]:
        query: Select[tuple[WorkspaceRuntime]] = select(WorkspaceRuntime).where(
            WorkspaceRuntime.workspace_id == workspace_id,
            WorkspaceRuntime.status != "deleted",
        )
        count_query = select(func.count()).select_from(WorkspaceRuntime).where(
            WorkspaceRuntime.workspace_id == workspace_id,
            WorkspaceRuntime.status != "deleted",
        )
        if status is not None:
            query = query.where(WorkspaceRuntime.status == status)
            count_query = count_query.where(WorkspaceRuntime.status == status)
        total = int(self._session.scalar(count_query) or 0)
        items = list(
            self._session.scalars(
                query.order_by(WorkspaceRuntime.created_at.desc()).limit(limit).offset(offset)
            )
        )
        return items, total

    def get_runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime | None:
        return self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.id == runtime_id,
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.status != "deleted",
            )
        )

    def start_runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime | None:
        runtime = self.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return None
        return self._manager.start_runtime(runtime)

    def stop_runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime | None:
        runtime = self.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return None
        return self._manager.stop_runtime(runtime)

    def delete_runtime(self, workspace_id: UUID, runtime_id: UUID) -> bool:
        runtime = self.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return False
        self._manager.delete_runtime(runtime)
        return True

    def execute_command(
        self,
        *,
        workspace_id: UUID,
        runtime_id: UUID,
        command: list[str],
    ) -> RuntimeCommand | None:
        runtime = self.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return None
        return self._manager.execute_command(
            workspace_id=workspace_id,
            runtime=runtime,
            command=command,
        )

    def list_commands(
        self,
        workspace_id: UUID,
        runtime_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[RuntimeCommand], int] | None:
        if self.get_runtime(workspace_id, runtime_id) is None:
            return None
        count_query = select(func.count()).select_from(RuntimeCommand).where(
            RuntimeCommand.workspace_id == workspace_id,
            RuntimeCommand.workspace_runtime_id == runtime_id,
        )
        total = int(self._session.scalar(count_query) or 0)
        items = list(
            self._session.scalars(
                select(RuntimeCommand)
                .where(
                    RuntimeCommand.workspace_id == workspace_id,
                    RuntimeCommand.workspace_runtime_id == runtime_id,
                )
                .order_by(RuntimeCommand.started_at.desc().nullslast(), RuntimeCommand.id)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total

    def list_events(
        self,
        workspace_id: UUID,
        runtime_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[RuntimeEvent], int] | None:
        if self.get_runtime(workspace_id, runtime_id) is None:
            return None
        count_query = select(func.count()).select_from(RuntimeEvent).where(
            RuntimeEvent.workspace_id == workspace_id,
            RuntimeEvent.workspace_runtime_id == runtime_id,
        )
        total = int(self._session.scalar(count_query) or 0)
        items = list(
            self._session.scalars(
                select(RuntimeEvent)
                .where(
                    RuntimeEvent.workspace_id == workspace_id,
                    RuntimeEvent.workspace_runtime_id == runtime_id,
                )
                .order_by(RuntimeEvent.created_at.desc(), RuntimeEvent.id)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total

    def _limits_from_template(self, template: RuntimeTemplate) -> RuntimeLimits:
        default_limits = template.default_limits or {}
        return RuntimeLimits(
            cpu_count=_as_float(default_limits.get("cpu_count"), 1),
            memory_mb=_as_int(default_limits.get("memory_mb"), 512),
            disk_mb=_as_int(default_limits.get("disk_mb"), 1024),
            timeout_seconds=_as_int(default_limits.get("timeout_seconds"), 60),
        )


def _as_float(value: object, fallback: float) -> float:
    if isinstance(value, int | float | str):
        return float(value)
    return fallback


def _as_int(value: object, fallback: int) -> int:
    if isinstance(value, int | float | str):
        return int(value)
    return fallback
