from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.capabilities.mcp_servers import (
    McpServerCreateRequest,
    McpServerHealthCheckRequest,
    McpServerUpdateRequest,
    McpToolAllowRequest,
    McpToolAllowUpdateRequest,
)
from backend.app.capabilities.mcp.catalog import McpCatalogServer
from backend.app.capabilities.mcp.catalog_service import McpCatalogService
from backend.app.capabilities.mcp.server_helpers import (
    mcp_health_error,
    require_mcp_server,
)
from backend.app.capabilities.mcp.server_rules import connection_summary
from backend.app.capabilities.models import McpServer, McpToolAllowlist
from backend.app.capabilities.schema_validation import (
    normalize_object_schema,
    reject_embedded_secrets,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DomainError
from backend.app.core.pagination import PageParams
from backend.app.db.errors import commit_or_raise_conflict, flush_or_raise_conflict
from backend.app.observability.audit_service import AuditService
from backend.app.reviews.approval_service import ResourceReviewApprovalService
from backend.app.reviews.constants import (
    RESOURCE_STATUS_ACTIVE,
    RESOURCE_STATUS_PENDING_APPROVAL,
    REVIEW_TYPE_MCP_SERVER,
    REVIEW_TYPE_MCP_TOOL_ALLOWLIST,
)
from backend.app.reviews.service import ResourcePolicyReviewBuilder


class McpServerService:
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def create_mcp_server(
        self,
        workspace_id: UUID,
        data: McpServerCreateRequest,
        actor_user_id: UUID | None = None,
        *,
        commit: bool = True,
    ) -> McpServer:
        review = ResourcePolicyReviewBuilder(self._session, self._settings).review_mcp_server(
            workspace_id=workspace_id,
            server_type=data.server_type,
            connection=data.connection,
            visibility=data.visibility,
        )
        server = McpServer(
            workspace_id=workspace_id,
            status=RESOURCE_STATUS_PENDING_APPROVAL if review.required else RESOURCE_STATUS_ACTIVE,
            **data.model_dump(),
        )
        self._session.add(server)
        flush_or_raise_conflict(self._session, "MCP server name already exists")
        if review.required:
            ResourceReviewApprovalService(self._session).request_resource_review(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                approval_type=REVIEW_TYPE_MCP_SERVER,
                target_type="mcp_server",
                target_id=server.id,
                target_name=server.name,
                review=review,
                snapshot={
                    "id": str(server.id),
                    "name": server.name,
                    "server_type": server.server_type,
                    "visibility": server.visibility,
                    "status": server.status,
                    "connection": dict(server.connection),
                },
            )
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_server.created"
                if not review.required
                else "mcp_server.review_requested",
                target_type="mcp_server",
                target_id=server.id,
                metadata={
                    "name": server.name,
                    "server_type": server.server_type,
                    "review_required": review.required,
                    "review_risk_level": review.risk_level,
                    "review_reasons": review.reasons,
                },
            )
        if commit:
            commit_or_raise_conflict(self._session, "MCP server name already exists")
            self._session.refresh(server)
        return server

    def update_mcp_server(
        self,
        workspace_id: UUID,
        mcp_server_id: UUID,
        data: McpServerUpdateRequest,
        actor_user_id: UUID | None = None,
    ) -> McpServer:
        server = require_mcp_server(self._session, workspace_id, mcp_server_id)
        if data.connection is None and data.visibility is None:
            raise ValueError("Provide connection or visibility to update MCP server")

        next_connection = data.connection if data.connection is not None else server.connection
        next_visibility = data.visibility or server.visibility
        review = ResourcePolicyReviewBuilder(self._session, self._settings).review_mcp_server(
            workspace_id=workspace_id,
            server_type=server.server_type,
            connection=next_connection,
            visibility=next_visibility,
        )
        previous_connection = connection_summary(server)
        previous_visibility = server.visibility
        connection_changed = data.connection is not None
        if data.connection is not None:
            server.connection = dict(data.connection)
        if data.visibility is not None:
            server.visibility = data.visibility
        if connection_changed:
            server.health_status = "unknown"
            server.last_health_check_at = None
            server.last_error = None
        if review.required:
            server.status = RESOURCE_STATUS_PENDING_APPROVAL
            ResourceReviewApprovalService(self._session).request_resource_review(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                approval_type=REVIEW_TYPE_MCP_SERVER,
                target_type="mcp_server",
                target_id=server.id,
                target_name=server.name,
                review=review,
                snapshot={
                    "id": str(server.id),
                    "name": server.name,
                    "server_type": server.server_type,
                    "visibility": server.visibility,
                    "status": server.status,
                    "connection": dict(server.connection),
                },
            )
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_server.review_requested" if review.required else "mcp_server.updated",
                target_type="mcp_server",
                target_id=server.id,
                metadata={
                    "name": server.name,
                    "server_type": server.server_type,
                    "previous_connection": previous_connection,
                    "connection": connection_summary(server),
                    "previous_visibility": previous_visibility,
                    "visibility": server.visibility,
                    "health_reset": connection_changed,
                    "review_required": review.required,
                    "review_risk_level": review.risk_level,
                    "review_reasons": review.reasons,
                },
            )
        self._session.commit()
        self._session.refresh(server)
        return server

    def list_mcp_servers(self, workspace_id: UUID, page: PageParams) -> tuple[list[McpServer], int]:
        return self._catalog().list_mcp_servers(workspace_id, page)

    def allow_mcp_tool(
        self,
        workspace_id: UUID,
        mcp_server_id: UUID,
        data: McpToolAllowRequest,
        actor_user_id: UUID | None = None,
        *,
        commit: bool = True,
    ) -> McpToolAllowlist:
        server = require_mcp_server(self._session, workspace_id, mcp_server_id)
        try:
            normalized_schema = normalize_object_schema(data.input_schema)
            reject_embedded_secrets(normalized_schema, path="input_schema")
            reject_embedded_secrets(data.policy, path="policy")
        except ValueError as exc:
            raise DomainError(
                str(exc),
                code="mcp_tool_schema_invalid",
                status_code=422,
            ) from exc
        normalized_data = data.model_copy(update={"input_schema": normalized_schema})
        review = ResourcePolicyReviewBuilder(
            self._session,
            self._settings,
        ).review_mcp_tool_allowlist(
            workspace_id=workspace_id,
            visibility=server.visibility,
            tool_name=normalized_data.tool_name,
            requires_approval=normalized_data.requires_approval,
            risk_level=normalized_data.risk_level,
            policy=normalized_data.policy,
        )
        allow = McpToolAllowlist(
            workspace_id=workspace_id,
            mcp_server_id=mcp_server_id,
            status=RESOURCE_STATUS_PENDING_APPROVAL if review.required else RESOURCE_STATUS_ACTIVE,
            **normalized_data.model_dump(),
        )
        self._session.add(allow)
        flush_or_raise_conflict(self._session, "MCP tool is already allowed for this server")
        if review.required:
            ResourceReviewApprovalService(self._session).request_resource_review(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                approval_type=REVIEW_TYPE_MCP_TOOL_ALLOWLIST,
                target_type="mcp_tool_allowlist",
                target_id=allow.id,
                target_name=allow.tool_name,
                review=review,
                snapshot={
                    "id": str(allow.id),
                    "mcp_server_id": str(allow.mcp_server_id),
                    "tool_name": allow.tool_name,
                    "description": allow.description,
                    "input_schema": dict(allow.input_schema),
                    "capability_key": allow.capability_key,
                    "requires_approval": allow.requires_approval,
                    "risk_level": allow.risk_level,
                    "policy": dict(allow.policy),
                    "status": allow.status,
                },
            )
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_tool.allowed" if not review.required else "mcp_tool.review_requested",
                target_type="mcp_tool_allowlist",
                target_id=allow.id,
                metadata={
                    "mcp_server_id": str(mcp_server_id),
                    "tool_name": allow.tool_name,
                    "risk_level": allow.risk_level,
                    "review_required": review.required,
                    "review_risk_level": review.risk_level,
                    "review_reasons": review.reasons,
                },
            )
        if commit:
            commit_or_raise_conflict(self._session, "MCP tool is already allowed for this server")
            self._session.refresh(allow)
        return allow

    def update_mcp_tool(
        self,
        workspace_id: UUID,
        mcp_server_id: UUID,
        allowlist_id: UUID,
        data: McpToolAllowUpdateRequest,
        actor_user_id: UUID | None = None,
    ) -> McpToolAllowlist:
        server = require_mcp_server(self._session, workspace_id, mcp_server_id)
        allow = self._session.scalar(
            select(McpToolAllowlist).where(
                McpToolAllowlist.workspace_id == workspace_id,
                McpToolAllowlist.mcp_server_id == mcp_server_id,
                McpToolAllowlist.id == allowlist_id,
            )
        )
        if allow is None:
            raise ValueError("MCP tool allowlist entry not found")
        changes = data.model_dump(exclude_unset=True)
        next_input_schema = (
            data.input_schema if data.input_schema is not None else allow.input_schema
        )
        next_policy = data.policy if data.policy is not None else allow.policy
        try:
            normalized_schema = normalize_object_schema(next_input_schema)
            reject_embedded_secrets(normalized_schema, path="input_schema")
            reject_embedded_secrets(next_policy, path="policy")
        except ValueError as exc:
            raise DomainError(str(exc), code="mcp_tool_schema_invalid", status_code=422) from exc
        next_tool_name = data.tool_name or allow.tool_name
        next_requires_approval = (
            data.requires_approval
            if data.requires_approval is not None
            else allow.requires_approval
        )
        next_risk_level = data.risk_level or allow.risk_level
        review = ResourcePolicyReviewBuilder(
            self._session,
            self._settings,
        ).review_mcp_tool_allowlist(
            workspace_id=workspace_id,
            visibility=server.visibility,
            tool_name=next_tool_name,
            requires_approval=next_requires_approval,
            risk_level=next_risk_level,
            policy=next_policy,
        )
        before = {
            "tool_name": allow.tool_name,
            "description": allow.description,
            "capability_key": allow.capability_key,
            "requires_approval": allow.requires_approval,
            "risk_level": allow.risk_level,
            "policy": dict(allow.policy),
            "status": allow.status,
        }
        for field_name, value in changes.items():
            setattr(allow, field_name, value)
        allow.input_schema = normalized_schema
        allow.policy = dict(next_policy)
        if review.required:
            allow.status = RESOURCE_STATUS_PENDING_APPROVAL
            ResourceReviewApprovalService(self._session).request_resource_review(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                approval_type=REVIEW_TYPE_MCP_TOOL_ALLOWLIST,
                target_type="mcp_tool_allowlist",
                target_id=allow.id,
                target_name=allow.tool_name,
                review=review,
                snapshot={
                    "id": str(allow.id),
                    "mcp_server_id": str(allow.mcp_server_id),
                    "tool_name": allow.tool_name,
                    "description": allow.description,
                    "input_schema": dict(allow.input_schema),
                    "capability_key": allow.capability_key,
                    "requires_approval": allow.requires_approval,
                    "risk_level": allow.risk_level,
                    "policy": dict(allow.policy),
                    "status": allow.status,
                },
            )
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_tool.review_requested" if review.required else "mcp_tool.updated",
                target_type="mcp_tool_allowlist",
                target_id=allow.id,
                metadata={
                    "mcp_server_id": str(mcp_server_id),
                    "changed_fields": sorted(changes),
                    "before": before,
                    "after": {
                        "tool_name": allow.tool_name,
                        "description": allow.description,
                        "capability_key": allow.capability_key,
                        "requires_approval": allow.requires_approval,
                        "risk_level": allow.risk_level,
                        "status": allow.status,
                    },
                    "review_required": review.required,
                },
            )
        commit_or_raise_conflict(self._session, "MCP tool is already allowed for this server")
        self._session.refresh(allow)
        return allow

    def disable_mcp_server(
        self,
        workspace_id: UUID,
        mcp_server_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> McpServer:
        server = require_mcp_server(self._session, workspace_id, mcp_server_id)
        server.status = "disabled"
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_server.disabled",
                target_type="mcp_server",
                target_id=server.id,
                metadata={"name": server.name},
            )
        self._session.commit()
        self._session.refresh(server)
        return server

    def record_mcp_server_health_check(
        self,
        workspace_id: UUID,
        mcp_server_id: UUID,
        actor_user_id: UUID | None,
        data: McpServerHealthCheckRequest,
    ) -> McpServer:
        server = require_mcp_server(self._session, workspace_id, mcp_server_id)
        previous = {
            "health_status": server.health_status,
            "last_health_check_at": server.last_health_check_at.isoformat()
            if server.last_health_check_at is not None
            else None,
            "last_error_configured": server.last_error is not None,
        }
        server.health_status = data.health_status
        server.last_health_check_at = datetime.now(UTC)
        server.last_error = mcp_health_error(data.health_status, data.error_code)
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_server.health_check_recorded",
                target_type="mcp_server",
                target_id=server.id,
                metadata={
                    "name": server.name,
                    "previous": previous,
                    "health_status": server.health_status,
                    "error_code": server.last_error,
                    "last_error_configured": server.last_error is not None,
                },
            )
        self._session.commit()
        self._session.refresh(server)
        return server

    def disable_mcp_tool(
        self,
        workspace_id: UUID,
        mcp_server_id: UUID,
        allowlist_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> McpToolAllowlist:
        require_mcp_server(self._session, workspace_id, mcp_server_id)
        allow = self._session.get(McpToolAllowlist, allowlist_id)
        if (
            allow is None
            or allow.workspace_id != workspace_id
            or allow.mcp_server_id != mcp_server_id
        ):
            raise ValueError("MCP tool allowlist entry not found")
        allow.status = "disabled"
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_tool.disabled",
                target_type="mcp_tool_allowlist",
                target_id=allow.id,
                metadata={
                    "mcp_server_id": str(mcp_server_id),
                    "tool_name": allow.tool_name,
                },
            )
        self._session.commit()
        self._session.refresh(allow)
        return allow

    def list_allowed_mcp_tools(
        self,
        workspace_id: UUID,
    ) -> list[tuple[McpToolAllowlist, McpServer]]:
        return self._catalog().list_allowed_mcp_tools(workspace_id)

    def list_mcp_catalog(
        self,
        workspace_id: UUID,
        page: PageParams,
        agent_profile_id: UUID | None = None,
    ) -> tuple[list[McpCatalogServer], int]:
        return self._catalog().list_mcp_catalog(
            workspace_id,
            page,
            agent_profile_id=agent_profile_id,
        )

    def mcp_tools_for_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
    ) -> list[tuple[McpToolAllowlist, McpServer]]:
        return self._catalog().mcp_tools_for_agent(workspace_id, agent_profile_id)

    def _catalog(self) -> McpCatalogService:
        return McpCatalogService(self._session, self._settings)
