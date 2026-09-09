from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.runtime_manager.contracts import RuntimeLimits
from backend.app.runtime_manager.manager_factory import RuntimeManagerFactory
from backend.app.runtime_manager.runtime_policy import RuntimePolicyResolution, limits_metadata
from backend.app.runtime_manager.safety import RuntimeSafetyPolicy
from backend.app.runtime_manager.template_guard import RuntimeTemplateGuard
from backend.app.runtimes.models import RuntimeEvent, RuntimeTemplate, WorkspaceRuntime


class RuntimeProvisioningService:
    def __init__(
        self,
        session: Session,
        safety: RuntimeSafetyPolicy,
        manager_factory: RuntimeManagerFactory,
    ) -> None:
        self._session = session
        self._safety = safety
        self._manager_factory = manager_factory

    def create_runtime(
        self,
        *,
        workspace_id: UUID,
        template_id: UUID,
        name: str,
        limits: RuntimeLimits | None,
        network_disabled: bool,
        runtime_space_id: UUID | None,
    ) -> WorkspaceRuntime | None:
        resolution = self._resolve_policy(
            workspace_id=workspace_id,
            template_id=template_id,
            limits=limits,
            network_disabled=network_disabled,
            runtime_space_id=runtime_space_id,
        )
        if resolution is None:
            return None
        template, policy = resolution
        return self._manager_factory.require().create_runtime(
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
        runtime_space_id: UUID | None,
        requested_by_user_id: UUID | None,
    ) -> WorkspaceRuntime | None:
        resolution = self._resolve_policy(
            workspace_id=workspace_id,
            template_id=template_id,
            limits=limits,
            network_disabled=network_disabled,
            runtime_space_id=runtime_space_id,
        )
        if resolution is None:
            return None
        template, policy = resolution
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
        runtime: WorkspaceRuntime,
        workspace_id: UUID,
        template_id: UUID,
        name: str,
        limits: RuntimeLimits | None,
        network_disabled: bool,
        runtime_space_id: UUID | None,
    ) -> WorkspaceRuntime | None:
        resolution = self._resolve_policy(
            workspace_id=workspace_id,
            template_id=template_id,
            limits=limits,
            network_disabled=network_disabled,
            runtime_space_id=runtime_space_id,
        )
        if resolution is None:
            return None
        template, policy = resolution
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
        return self._manager_factory.require().provision_runtime(
            runtime,
            template=template,
            limits=policy.limits,
            network_disabled=policy.network_disabled,
            policy_metadata=policy.metadata,
        )

    def _resolve_policy(
        self,
        *,
        workspace_id: UUID,
        template_id: UUID,
        limits: RuntimeLimits | None,
        network_disabled: bool,
        runtime_space_id: UUID | None,
    ) -> tuple[RuntimeTemplate, RuntimePolicyResolution] | None:
        guard = RuntimeTemplateGuard(self._session, self._safety)
        template = guard.validated_template(
            workspace_id=workspace_id,
            template_id=template_id,
            limits=limits,
            network_disabled=network_disabled,
            runtime_space_id=runtime_space_id,
        )
        if template is None:
            return None
        return template, guard.resolve_runtime_policy(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            template=template,
            requested_limits=limits,
            requested_network_disabled=network_disabled,
        )
