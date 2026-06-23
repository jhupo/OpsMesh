from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.reviews.models import ResourceReview
from backend.app.reviews.scanner import ReviewScanner
from backend.app.reviews.semantic_runner import SemanticResourceReviewRunner
from backend.app.reviews.utils import (
    _HIGH_RISK_LEVELS,
    _connection_has_external_url,
    _has_sensitive_keys,
    _list_from_manifest,
    _normalize_risk,
    _policy_mode,
)
from backend.app.security.redaction import redact_sensitive_payload


class ResourcePolicyReviewBuilder:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._semantic = SemanticResourceReviewRunner(session, settings)

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
        scanner = ReviewScanner()
        scanner.scan_text("name", name)
        scanner.scan_text("role", role)
        scanner.scan_text("instructions", instructions)
        scanner.scan_mapping("capabilities", capabilities)
        scanner.scan_mapping("skills", skills)
        scanner.scan_mapping("tool_policy", tool_policy)
        scanner.scan_mapping("runtime_policy", runtime_policy)
        scanner.scan_mapping("approval_policy", approval_policy)
        if _policy_mode(tool_policy) in {"all", "allow_all", "unrestricted"}:
            scanner.add("high", "tool_policy.allows_unrestricted_tools")
        if runtime_policy:
            scanner.add("medium", "runtime_policy.configured")
        return self._review(
            workspace_id=workspace_id,
            resource_type="agent_profile",
            visibility=visibility,
            scanner=scanner,
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
        scanner = ReviewScanner()
        scanner.scan_text("visibility", visibility)
        scanner.scan_mapping("manifest", manifest)
        for key in capability_keys:
            scanner.scan_text("capability_key", key)
        if visibility == "public":
            scanner.add("medium", "skill.public_visibility")
        required_tools = _list_from_manifest(manifest, "required_tools")
        if required_tools:
            scanner.add("medium", "skill.requires_tools")
            for tool_name in required_tools:
                scanner.scan_text("required_tool", tool_name)
        return self._review(
            workspace_id=workspace_id,
            resource_type="skill",
            visibility=visibility,
            scanner=scanner,
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
        scanner = ReviewScanner()
        scanner.scan_text("key", key)
        scanner.scan_text("name", name)
        scanner.scan_text("category", category)
        scanner.scan_text("description", description)
        scanner.scan_mapping("default_policy", default_policy)
        if _policy_mode(default_policy) in {"all", "allow_all", "unrestricted"}:
            scanner.add("high", "capability.default_policy.allows_unrestricted_access")
        return self._review(
            workspace_id=workspace_id,
            resource_type="capability",
            visibility="public",
            scanner=scanner,
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
        scanner = ReviewScanner()
        scanner.scan_text("server_type", server_type)
        scanner.scan_text("visibility", visibility)
        scanner.scan_mapping("connection", connection)
        if server_type in {"docker", "self_hosted"}:
            scanner.add("high", f"mcp_server.execution_mode.{server_type}")
        elif server_type == "stdio":
            scanner.add("medium", "mcp_server.execution_mode.stdio")
        if visibility == "public":
            scanner.add("medium", "mcp_server.public_visibility")
        if _connection_has_external_url(connection):
            scanner.add("medium", "mcp_server.external_connection")
        return self._review(
            workspace_id=workspace_id,
            resource_type="mcp_server",
            visibility=visibility,
            scanner=scanner,
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
        scanner = ReviewScanner()
        scanner.scan_text("tool_name", tool_name)
        scanner.scan_mapping("policy", policy)
        normalized_risk = _normalize_risk(risk_level)
        if requires_approval:
            scanner.add("medium", "mcp_tool.requires_runtime_approval")
        if normalized_risk in _HIGH_RISK_LEVELS:
            scanner.add(normalized_risk, f"mcp_tool.risk_level.{normalized_risk}")
        return self._review(
            workspace_id=workspace_id,
            resource_type="mcp_tool_allowlist",
            visibility=visibility,
            scanner=scanner,
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
        scanner = ReviewScanner()
        scanner.scan_text("name", name)
        scanner.scan_text("provider", provider)
        scanner.scan_text("external_ref", external_ref)
        for scope in scopes:
            scanner.scan_text("scope", scope)
        scanner.add(
            "high" if mcp_server_id is None else "medium",
            _credential_scope_signal(mcp_server_id),
        )
        if has_secret_payload:
            scanner.add("high", "mcp_credential.hosted_secret")
        return self._review(
            workspace_id=workspace_id,
            resource_type="mcp_credential_reference",
            visibility=visibility,
            scanner=scanner,
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
        self,
        *,
        workspace_id: UUID | None,
        visibility: str,
        name: str,
        manifest: dict[str, object],
    ) -> ResourceReview:
        scanner = ReviewScanner()
        scanner.scan_text("visibility", visibility)
        scanner.scan_text("name", name)
        scanner.scan_mapping("manifest", manifest)
        if visibility == "public":
            scanner.add("medium", "plugin.public_visibility")
        for permission in _list_from_manifest(manifest, "permissions"):
            scanner.scan_text("permission", permission)
        return self._review(
            workspace_id=workspace_id,
            resource_type="plugin",
            visibility=visibility,
            scanner=scanner,
            resource={"visibility": visibility, "name": name, "manifest": manifest},
        )

    def review_tool_execution(
        self,
        *,
        workspace_id: UUID | None,
        tool_kind: str,
        tool_name: str,
        arguments: dict[str, object],
        static_signals: dict[str, object],
        context: dict[str, object],
    ) -> ResourceReview:
        scanner = ReviewScanner()
        scanner.scan_text("tool_kind", tool_kind)
        scanner.scan_text("tool_name", tool_name)
        scanner.scan_mapping("arguments", arguments)
        scanner.scan_mapping("context", context)
        if _has_sensitive_keys(arguments):
            scanner.add("high", "tool_execution.arguments.contains_sensitive_keys")
        return self._review(
            workspace_id=workspace_id,
            resource_type="tool_execution",
            visibility="public",
            scanner=scanner,
            resource={
                "tool_kind": tool_kind,
                "tool_name": tool_name,
                "arguments": redact_sensitive_payload(arguments),
                "context": redact_sensitive_payload(context),
                "static_signals": redact_sensitive_payload(static_signals),
            },
        )

    def _review(
        self,
        *,
        workspace_id: UUID | None,
        resource_type: str,
        visibility: str,
        resource: dict[str, object],
        scanner: ReviewScanner,
    ) -> ResourceReview:
        return self._semantic.review_with_policy_signals(
            workspace_id=workspace_id,
            resource_type=resource_type,
            visibility=visibility,
            resource=resource,
            scanner=scanner,
        )


def _credential_scope_signal(mcp_server_id: UUID | None) -> str:
    if mcp_server_id is None:
        return "mcp_credential.workspace_wide_scope"
    return "mcp_credential.server_scope"
