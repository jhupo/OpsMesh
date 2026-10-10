from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.governance.reviews.configured import ConfiguredApprovalService
from opsmesh.governance.reviews.models import ResourceReview
from opsmesh.shared.config import Settings


class ResourcePolicyReviewBuilder:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._reviewer = ConfiguredApprovalService(session, settings)

    def review_agent_profile(
        self,
        *,
        workspace_id: UUID | None,
        visibility: str = "private",
        name: str,
        role: str,
        instructions: str,
        capabilities: dict[str, object],
        skills: dict[str, object],
        tool_policy: dict[str, object],
        runtime_policy: dict[str, object],
        approval_policy: dict[str, object],
    ) -> ResourceReview:
        return self._review(
            workspace_id=workspace_id,
            resource_type="agent_profile",
            visibility=visibility,
            resource={
                "visibility": visibility,
                "name": name,
                "role": role,
                "instructions": instructions,
                "capabilities": capabilities,
                "skills": skills,
                "tool_policy": tool_policy,
                "runtime_policy": runtime_policy,
                "approval_policy": approval_policy,
            },
        )

    def review_skill(
        self,
        *,
        workspace_id: UUID | None,
        visibility: str,
        manifest: dict[str, object],
        capability_keys: list[str],
    ) -> ResourceReview:
        return self._review(
            workspace_id=workspace_id,
            resource_type="skill",
            visibility=visibility,
            resource={
                "visibility": visibility,
                "manifest": manifest,
                "capability_keys": capability_keys,
            },
        )

    def review_capability(
        self,
        *,
        workspace_id: UUID | None,
        key: str,
        name: str,
        category: str,
        description: str,
        default_policy: dict[str, object],
    ) -> ResourceReview:
        return self._review(
            workspace_id=workspace_id,
            resource_type="capability",
            visibility="public",
            resource={
                "key": key,
                "name": name,
                "category": category,
                "description": description,
                "default_policy": default_policy,
            },
        )

    def review_mcp_server(
        self,
        *,
        workspace_id: UUID | None,
        server_type: str,
        connection: dict[str, object],
        visibility: str,
    ) -> ResourceReview:
        return self._review(
            workspace_id=workspace_id,
            resource_type="mcp_server",
            visibility=visibility,
            resource={
                "server_type": server_type,
                "connection": connection,
                "visibility": visibility,
            },
        )

    def review_mcp_tool_allowlist(
        self,
        *,
        workspace_id: UUID | None,
        visibility: str = "private",
        tool_name: str,
        requires_approval: bool,
        risk_level: str,
        policy: dict[str, object],
    ) -> ResourceReview:
        return self._review(
            workspace_id=workspace_id,
            resource_type="mcp_tool_allowlist",
            visibility=visibility,
            resource={
                "visibility": visibility,
                "tool_name": tool_name,
                "requires_approval": requires_approval,
                "risk_level": risk_level,
                "policy": policy,
            },
        )

    def review_mcp_credential_reference(
        self,
        *,
        workspace_id: UUID | None,
        visibility: str = "private",
        mcp_server_id: UUID | None,
        name: str,
        provider: str,
        external_ref: str,
        scopes: list[str],
        has_secret_payload: bool,
    ) -> ResourceReview:
        return self._review(
            workspace_id=workspace_id,
            resource_type="mcp_credential_reference",
            visibility=visibility,
            resource={
                "visibility": visibility,
                "mcp_server_id": str(mcp_server_id) if mcp_server_id is not None else None,
                "name": name,
                "provider": provider,
                "external_ref": external_ref,
                "scopes": scopes,
                "has_secret_payload": has_secret_payload,
            },
        )

    def review_plugin(
        self, *, workspace_id: UUID | None, visibility: str, name: str, manifest: dict[str, object]
    ) -> ResourceReview:
        return self._review(
            workspace_id=workspace_id,
            resource_type="plugin",
            visibility=visibility,
            resource={"visibility": visibility, "name": name, "manifest": manifest},
        )


    def _review(
        self,
        *,
        workspace_id: UUID | None,
        resource_type: str,
        visibility: str,
        resource: dict[str, object],
    ) -> ResourceReview:
        if workspace_id is None:
            raise ValueError("Workspace is required for resource approval configuration")
        review = self._reviewer.review(
            workspace_id=workspace_id,
            action="resource",
            name=resource_type,
            arguments=resource,
            context={"visibility": visibility},
        )
        if review.blocked:
            raise ValueError("Resource denied by configured approval policy")
        return ResourceReview(review.required, review.risk_level, review.reasons, review.signals)
