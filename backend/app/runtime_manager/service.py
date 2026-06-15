from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.admin.policies import PlatformPolicyService
from backend.app.core.config import Settings
from backend.app.runtime_manager.contracts import DockerRuntimeClient, RuntimeLimits
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtime_manager.runtime_policy import (
    RuntimePolicyResolution,
    RuntimePolicyResolver,
    limits_metadata,
)
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
        settings: Settings,
        docker_client: DockerRuntimeClient | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._docker_client = docker_client
        self._manager_instance: RuntimeManager | None = None
        self._safety = RuntimeSafetyPolicy(
            tuple(settings.runtime_allowed_images),
            PlatformPolicyService(session).risky_execution_policy(),
        )

    @property
    def _manager(self) -> RuntimeManager:
        if self._docker_client is None:
            raise RuntimeError("Runtime manager execution requires a worker-injected Docker client")
        if self._manager_instance is None:
            self._manager_instance = RuntimeManager(
                self._session,
                self._docker_client,
                managed_host_roots=[Path(self._settings.storage_root).resolve() / "runtimes"],
            )
        return self._manager_instance

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
        policy = self._resolve_runtime_policy(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            template=template,
            requested_limits=limits,
            requested_network_disabled=network_disabled,
        )
        self._safety.assert_template_allowed(template)
        self._safety.assert_network_allowed(
            template,
            network_disabled=policy.network_disabled,
        )
        return self._manager.create_runtime(
            workspace_id=workspace_id,
            template=template,
            name=name,
            limits=policy.limits,
            runtime_space_id=runtime_space_id,
            network_disabled=policy.network_disabled,
            policy_metadata=policy.metadata,
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
    ) -> WorkspaceRuntime | None:
        template = self._validated_template(
            workspace_id=workspace_id,
            template_id=template_id,
            limits=limits,
            network_disabled=network_disabled,
            runtime_space_id=runtime_space_id,
        )
        if template is None:
            return None
        policy = self._resolve_runtime_policy(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            template=template,
            requested_limits=limits,
            requested_network_disabled=network_disabled,
        )
        runtime = WorkspaceRuntime(
            workspace_id=workspace_id,
            runtime_template_id=template.id,
            runtime_space_id=runtime_space_id,
            name=name,
            status="queued",
            connection_status="offline",
            limits=limits_metadata(policy.limits),
            network_policy={"disabled": policy.network_disabled},
            capabilities={
                "provisioning": {
                    "status": "queued",
                    "requested_by_user_id": str(requested_by_user_id)
                    if requested_by_user_id is not None
                    else None,
                    "queued_at": datetime.now(UTC).isoformat(),
                    "policy_resolution": policy.metadata,
                }
            },
        )
        self._session.add(runtime)
        self._session.flush()
        self._session.add(
            RuntimeEvent(
                workspace_id=workspace_id,
                workspace_runtime_id=runtime.id,
                runtime_space_id=runtime_space_id,
                event_type="runtime.provisioning_queued",
                message="Runtime provisioning queued for worker execution",
                event_metadata={
                    "runtime_id": str(runtime.id),
                    "template_id": str(template.id),
                    "requested_by_user_id": str(requested_by_user_id)
                    if requested_by_user_id is not None
                    else None,
                },
                created_at=datetime.now(UTC),
            )
        )
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

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
    ) -> WorkspaceRuntime | None:
        template = self._validated_template(
            workspace_id=workspace_id,
            template_id=template_id,
            limits=limits,
            network_disabled=network_disabled,
            runtime_space_id=runtime_space_id,
        )
        if template is None:
            return None
        runtime = self.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return None
        policy = self._resolve_runtime_policy(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            template=template,
            requested_limits=limits,
            requested_network_disabled=network_disabled,
        )
        runtime.runtime_template_id = template.id
        runtime.runtime_space_id = runtime_space_id
        runtime.name = name
        runtime.status = "provisioning"
        runtime.connection_status = "offline"
        runtime.limits = limits_metadata(policy.limits)
        runtime.network_policy = {"disabled": policy.network_disabled}
        runtime.capabilities = {
            **dict(runtime.capabilities or {}),
            "provisioning": {
                "status": "running",
                "started_at": datetime.now(UTC).isoformat(),
                "policy_resolution": policy.metadata,
            },
        }
        self._session.flush()
        return self._manager.provision_runtime(
            runtime,
            template=template,
            limits=policy.limits,
            network_disabled=policy.network_disabled,
            policy_metadata=policy.metadata,
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
        count_query = (
            select(func.count())
            .select_from(WorkspaceRuntime)
            .where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.status != "deleted",
            )
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
        record = RuntimeCommand(
            workspace_id=workspace_id,
            workspace_runtime_id=runtime.id,
            runtime_space_id=runtime.runtime_space_id,
            command=command,
            status="queued",
        )
        self._session.add(record)
        self._session.flush()
        self._session.add(
            RuntimeEvent(
                workspace_id=workspace_id,
                workspace_runtime_id=runtime.id,
                runtime_space_id=runtime.runtime_space_id,
                event_type="runtime.command.queued",
                message=" ".join(command),
                event_metadata={"runtime_id": str(runtime.id), "command_id": str(record.id)},
                created_at=datetime.now(UTC),
            )
        )
        self._session.commit()
        self._session.refresh(record)
        return record

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
        record = self._session.scalar(
            select(RuntimeCommand).where(
                RuntimeCommand.workspace_id == workspace_id,
                RuntimeCommand.workspace_runtime_id == runtime_id,
                RuntimeCommand.id == command_id,
            )
        )
        if record is None:
            return None
        if record.status != "queued":
            return record
        return self._manager.execute_existing_command(
            workspace_id=workspace_id,
            runtime=runtime,
            record=record,
            command=command,
        )

    def _validated_template(
        self,
        *,
        workspace_id: UUID,
        template_id: UUID,
        limits: RuntimeLimits | None,
        network_disabled: bool,
        runtime_space_id: UUID | None,
    ) -> RuntimeTemplate | None:
        template = self._session.get(RuntimeTemplate, template_id)
        if template is None:
            return None
        if runtime_space_id is not None:
            RuntimeSpaceService(self._session).require_runtime_space(
                workspace_id,
                runtime_space_id,
            )
        policy = self._resolve_runtime_policy(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            template=template,
            requested_limits=limits,
            requested_network_disabled=network_disabled,
        )
        self._safety.assert_template_allowed(template)
        self._safety.assert_network_allowed(
            template,
            network_disabled=policy.network_disabled,
        )
        return template

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
        count_query = (
            select(func.count())
            .select_from(RuntimeCommand)
            .where(
                RuntimeCommand.workspace_id == workspace_id,
                RuntimeCommand.workspace_runtime_id == runtime_id,
            )
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
        count_query = (
            select(func.count())
            .select_from(RuntimeEvent)
            .where(
                RuntimeEvent.workspace_id == workspace_id,
                RuntimeEvent.workspace_runtime_id == runtime_id,
            )
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

    def _resolve_runtime_policy(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID | None,
        template: RuntimeTemplate,
        requested_limits: RuntimeLimits | None,
        requested_network_disabled: bool,
    ) -> RuntimePolicyResolution:
        return RuntimePolicyResolver(self._session).resolve_runtime_policy(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            template=template,
            requested_limits=requested_limits,
            requested_network_disabled=requested_network_disabled,
        )
