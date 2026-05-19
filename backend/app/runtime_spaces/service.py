from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.runtime_spaces.models import (
    RuntimeSpace,
    RuntimeSpaceBinding,
    RuntimeSpaceEvent,
    RuntimeSpaceQuota,
)
from backend.app.runtimes.models import RuntimeTemplate
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam

TARGET_TYPES_BY_SCOPE = {
    "workspace": "workspace",
    "team": "agent_team",
    "task": "task",
}
T = TypeVar("T")


class RuntimeSpaceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_runtime_space(
        self,
        *,
        workspace_id: UUID,
        name: str,
        scope: str,
        target_id: UUID | None,
        created_by_user_id: UUID | None,
        default_runtime_template_id: UUID | None,
        policy: dict[str, object],
        network_policy: dict[str, object],
        storage_policy: dict[str, object],
        cleanup_policy: dict[str, object],
        quota_limits: dict[str, int],
    ) -> RuntimeSpace:
        normalized_target_id = self._normalize_target_id(
            workspace_id=workspace_id,
            scope=scope,
            target_id=target_id,
        )
        if default_runtime_template_id is not None:
            self._require_runtime_template(default_runtime_template_id)

        runtime_space = RuntimeSpace(
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            default_runtime_template_id=default_runtime_template_id,
            name=name,
            scope=scope,
            policy=policy,
            network_policy=network_policy,
            storage_policy=storage_policy,
            cleanup_policy=cleanup_policy,
        )
        self._session.add(runtime_space)
        self._session.flush([runtime_space])
        self._bind_target(
            workspace_id=workspace_id,
            runtime_space=runtime_space,
            target_id=normalized_target_id,
        )
        self._replace_quotas(
            workspace_id=workspace_id,
            runtime_space=runtime_space,
            quota_limits=quota_limits,
        )
        self._append_event(
            runtime_space,
            "runtime_space.created",
            f"Runtime space {runtime_space.name} created",
            {
                "scope": scope,
                "target_id": str(normalized_target_id),
            },
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space

    def list_runtime_spaces(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        status: str | None = None,
        scope: str | None = None,
    ) -> tuple[list[RuntimeSpace], int]:
        statement = select(RuntimeSpace).where(RuntimeSpace.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(RuntimeSpace.status == status)
        if scope is not None:
            statement = statement.where(RuntimeSpace.scope == scope)
        return self._page(statement.order_by(RuntimeSpace.created_at.desc()), page)

    def get_runtime_space(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> RuntimeSpace | None:
        return self._session.scalar(
            select(RuntimeSpace).where(
                RuntimeSpace.workspace_id == workspace_id,
                RuntimeSpace.id == runtime_space_id,
            )
        )

    def update_runtime_space(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        name: str | None,
        status: str | None,
        policy: dict[str, object] | None,
        network_policy: dict[str, object] | None,
        storage_policy: dict[str, object] | None,
        cleanup_policy: dict[str, object] | None,
        quota_limits: dict[str, int] | None,
    ) -> RuntimeSpace | None:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None:
            return None
        changed_fields: list[str] = []
        for field_name, value in (
            ("name", name),
            ("status", status),
            ("policy", policy),
            ("network_policy", network_policy),
            ("storage_policy", storage_policy),
            ("cleanup_policy", cleanup_policy),
        ):
            if value is not None:
                setattr(runtime_space, field_name, value)
                changed_fields.append(field_name)
        if quota_limits is not None:
            self._replace_quotas(
                workspace_id=workspace_id,
                runtime_space=runtime_space,
                quota_limits=quota_limits,
            )
            changed_fields.append("quota_limits")
        if changed_fields:
            self._append_event(
                runtime_space,
                "runtime_space.updated",
                f"Runtime space {runtime_space.name} updated",
                {"changed_fields": changed_fields},
            )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space

    def reset_runtime_space(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> RuntimeSpace | None:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None:
            return None
        self._append_event(
            runtime_space,
            "runtime_space.reset_requested",
            f"Runtime space {runtime_space.name} reset requested",
            {},
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space

    def list_events(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
        page: PageParams,
    ) -> tuple[list[RuntimeSpaceEvent], int] | None:
        if self.get_runtime_space(workspace_id, runtime_space_id) is None:
            return None
        statement = (
            select(RuntimeSpaceEvent)
            .where(
                RuntimeSpaceEvent.workspace_id == workspace_id,
                RuntimeSpaceEvent.runtime_space_id == runtime_space_id,
            )
            .order_by(RuntimeSpaceEvent.created_at.desc(), RuntimeSpaceEvent.id)
        )
        return self._page(statement, page)

    def require_runtime_space(self, workspace_id: UUID, runtime_space_id: UUID) -> RuntimeSpace:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None or runtime_space.status != "active":
            raise ValueError("Runtime space not found")
        return runtime_space

    def _normalize_target_id(
        self,
        *,
        workspace_id: UUID,
        scope: str,
        target_id: UUID | None,
    ) -> UUID:
        if scope == "workspace":
            return workspace_id
        if target_id is None:
            raise ValueError("target_id is required for team and task runtime spaces")
        if scope == "team":
            exists = self._session.scalar(
                select(AgentTeam.id).where(
                    AgentTeam.workspace_id == workspace_id,
                    AgentTeam.id == target_id,
                    AgentTeam.status == "active",
                )
            )
            if exists is None:
                raise ValueError("Team not found")
            return target_id
        if scope == "task":
            exists = self._session.scalar(
                select(Task.id).where(Task.workspace_id == workspace_id, Task.id == target_id)
            )
            if exists is None:
                raise ValueError("Task not found")
            return target_id
        raise ValueError("Invalid runtime space scope")

    def _require_runtime_template(self, template_id: UUID) -> None:
        template = self._session.scalar(
            select(RuntimeTemplate.id).where(
                RuntimeTemplate.id == template_id,
                RuntimeTemplate.status == "active",
            )
        )
        if template is None:
            raise ValueError("Runtime template not found")

    def _bind_target(
        self,
        *,
        workspace_id: UUID,
        runtime_space: RuntimeSpace,
        target_id: UUID,
    ) -> None:
        target_type = TARGET_TYPES_BY_SCOPE[runtime_space.scope]
        existing = self._session.scalar(
            select(RuntimeSpaceBinding).where(
                RuntimeSpaceBinding.workspace_id == workspace_id,
                RuntimeSpaceBinding.target_type == target_type,
                RuntimeSpaceBinding.target_id == target_id,
                RuntimeSpaceBinding.status == "active",
            )
        )
        if existing is not None:
            raise ValueError("Target already has an active runtime space")
        self._session.add(
            RuntimeSpaceBinding(
                workspace_id=workspace_id,
                runtime_space_id=runtime_space.id,
                target_type=target_type,
                target_id=target_id,
            )
        )

    def _replace_quotas(
        self,
        *,
        workspace_id: UUID,
        runtime_space: RuntimeSpace,
        quota_limits: dict[str, int],
    ) -> None:
        existing = self._session.scalars(
            select(RuntimeSpaceQuota).where(
                RuntimeSpaceQuota.workspace_id == workspace_id,
                RuntimeSpaceQuota.runtime_space_id == runtime_space.id,
            )
        ).all()
        existing_by_key = {quota.quota_key: quota for quota in existing}
        for quota_key, limit_value in quota_limits.items():
            quota = existing_by_key.pop(quota_key, None)
            if quota is None:
                quota = RuntimeSpaceQuota(
                    workspace_id=workspace_id,
                    runtime_space_id=runtime_space.id,
                    quota_key=quota_key,
                    limit_value=limit_value,
                )
                self._session.add(quota)
            else:
                quota.limit_value = limit_value
                quota.status = "active"
        for quota in existing_by_key.values():
            quota.status = "disabled"

    def _append_event(
        self,
        runtime_space: RuntimeSpace,
        event_type: str,
        message: str,
        metadata: dict[str, object],
    ) -> None:
        self._session.add(
            RuntimeSpaceEvent(
                workspace_id=runtime_space.workspace_id,
                runtime_space_id=runtime_space.id,
                event_type=event_type,
                message=message,
                event_metadata=metadata,
                created_at=datetime.now(UTC),
            )
        )

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)
