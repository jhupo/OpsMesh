from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.runtime_manager.contracts import RuntimeLimits
from backend.app.runtime_manager.runtime_policy import (
    RuntimePolicyResolution,
    RuntimePolicyResolver,
)
from backend.app.runtime_manager.safety import RuntimeSafetyPolicy
from backend.app.runtime_spaces.service import RuntimeSpaceService
from backend.app.runtimes.models import RuntimeTemplate


class RuntimeTemplateGuard:
    def __init__(self, session: Session, safety: RuntimeSafetyPolicy) -> None:
        self._session = session
        self._safety = safety

    def validated_template(
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
        policy = self.resolve_runtime_policy(
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
            egress_policy=policy.egress_policy,
        )
        return template

    def resolve_runtime_policy(
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
