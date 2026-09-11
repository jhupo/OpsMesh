from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.admin.policy_reader import PlatformPolicyService
from backend.app.core.config import Settings
from backend.app.runtime_manager.commands import RuntimeCommandService
from backend.app.runtime_manager.core.contracts import (
    DockerRuntimeClient,
    RuntimeExecutionMode,
    RuntimeLimits,
)
from backend.app.runtime_manager.manager_factory import RuntimeManagerFactory
from backend.app.runtime_manager.provisioning import RuntimeProvisioningService
from backend.app.runtime_manager.queries import RuntimeControlQueryService
from backend.app.runtime_manager.safety import RuntimeSafetyPolicy
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
        settings: Settings,
        docker_client: DockerRuntimeClient | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._manager_factory = RuntimeManagerFactory(session, settings, docker_client)
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
        execution_mode: RuntimeExecutionMode = "pooled",
        pool_key: str | None = None,
    ) -> WorkspaceRuntime | None:
        return self._provisioning().create_runtime(
            workspace_id=workspace_id,
            template_id=template_id,
            name=name,
            limits=limits,
            network_disabled=network_disabled,
            runtime_space_id=runtime_space_id,
            execution_mode=execution_mode,
            pool_key=pool_key,
        )

    def queue_runtime_create(
        self,
        *,
        workspace_id: UUID,
        template_id: UUID,
        name: str,
        limits: RuntimeLimits | None,
        network_disabled: bool,
        runtime_space_id: UUID | None = None,
        requested_by_user_id: UUID | None = None,
        execution_mode: RuntimeExecutionMode = "pooled",
        pool_key: str | None = None,
    ) -> WorkspaceRuntime | None:
        return self._provisioning().queue_runtime_create(
            workspace_id=workspace_id,
            template_id=template_id,
            name=name,
            limits=limits,
            network_disabled=network_disabled,
            runtime_space_id=runtime_space_id,
            requested_by_user_id=requested_by_user_id,
            execution_mode=execution_mode,
            pool_key=pool_key,
        )

    def complete_queued_runtime_create(
        self,
        *,
        workspace_id: UUID,
        runtime_id: UUID,
        template_id: UUID,
        name: str,
        limits: RuntimeLimits | None,
        network_disabled: bool,
        runtime_space_id: UUID | None = None,
        execution_mode: RuntimeExecutionMode = "pooled",
        pool_key: str | None = None,
    ) -> WorkspaceRuntime | None:
        runtime = self.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return None
        return self._provisioning().complete_queued_runtime_create(
            runtime=runtime,
            workspace_id=workspace_id,
            template_id=template_id,
            name=name,
            limits=limits,
            network_disabled=network_disabled,
            runtime_space_id=runtime_space_id,
            execution_mode=execution_mode,
            pool_key=pool_key,
        )

    def list_runtimes(
        self,
        workspace_id: UUID,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[list[WorkspaceRuntime], int]:
        return RuntimeControlQueryService(self._session).list_runtimes(
            workspace_id,
            limit=limit,
            offset=offset,
            status=status,
        )

    def get_runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime | None:
        return RuntimeControlQueryService(self._session).get_runtime(workspace_id, runtime_id)

    def start_runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime | None:
        runtime = self.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return None
        return self._manager_factory.require().start_runtime(runtime)

    def stop_runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime | None:
        runtime = self.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return None
        return self._manager_factory.require().stop_runtime(runtime)

    def delete_runtime(self, workspace_id: UUID, runtime_id: UUID) -> bool:
        runtime = self.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return False
        self._manager_factory.require().delete_runtime(runtime)
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
        return self._commands().execute_command(
            workspace_id=workspace_id,
            runtime=runtime,
            command=command,
        )

    def queue_command(
        self,
        *,
        workspace_id: UUID,
        runtime_id: UUID,
        command: list[str],
    ) -> RuntimeCommand | None:
        runtime = self.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return None
        return self._commands().queue_command(
            workspace_id=workspace_id,
            runtime=runtime,
            command=command,
        )

    def execute_queued_command(
        self,
        *,
        workspace_id: UUID,
        runtime_id: UUID,
        command_id: UUID,
        command: list[str],
    ) -> RuntimeCommand | None:
        runtime = self.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return None
        return self._commands().execute_queued_command(
            workspace_id=workspace_id,
            runtime=runtime,
            runtime_id=runtime_id,
            command_id=command_id,
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
        return RuntimeControlQueryService(self._session).list_commands(
            workspace_id,
            runtime_id,
            limit=limit,
            offset=offset,
        )

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
        return RuntimeControlQueryService(self._session).list_events(
            workspace_id,
            runtime_id,
            limit=limit,
            offset=offset,
        )

    def _provisioning(self) -> RuntimeProvisioningService:
        return RuntimeProvisioningService(self._session, self._safety, self._manager_factory)

    def _commands(self) -> RuntimeCommandService:
        return RuntimeCommandService(self._session, self._manager_factory, self._settings)
