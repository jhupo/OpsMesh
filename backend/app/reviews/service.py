from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.approvals.models import Approval
from backend.app.approvals.service import ApprovalService
from backend.app.audit.service import AuditService
from backend.app.core.config import Settings
from backend.app.model_providers.service import (
    ModelProviderCredentialService,
    ModelProviderUnavailableError,
)
from backend.app.reviews.config import ResourceReviewSettings
from backend.app.reviews.llm import LlmResourceReviewer
from backend.app.reviews.llm_review import (
    llm_unavailable_review,
    merge_policy_and_llm_reviews,
    review_skipped,
)
from backend.app.reviews.models import ResourceReview
from backend.app.reviews.scanner import ReviewScanner
from backend.app.reviews.utils import (
    _HIGH_RISK_LEVELS,
    _connection_has_external_url,
    _has_sensitive_keys,
    _list_from_manifest,
    _normalize_risk,
    _policy_mode,
)
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.redaction import redact_sensitive_payload


class ResourceReviewService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings

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
        resource = {
            "visibility": visibility,
            "name": name,
            "role": role,
            "instructions": instructions,
            "capabilities": capabilities,
            "skills": skills,
            "tool_policy": tool_policy,
            "runtime_policy": runtime_policy,
            "approval_policy": approval_policy,
        }
        return self._semantic_review_with_policy_signals(
            workspace_id=workspace_id,
            resource_type="agent_profile",
            visibility=visibility,
            resource=resource,
            scanner=scanner,
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
        return self._semantic_review_with_policy_signals(
            workspace_id=workspace_id,
            resource_type="skill",
            visibility=visibility,
            resource={
                "visibility": visibility,
                "manifest": manifest,
                "capability_keys": capability_keys,
            },
            scanner=scanner,
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
        return self._semantic_review_with_policy_signals(
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
            scanner=scanner,
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
        return self._semantic_review_with_policy_signals(
            workspace_id=workspace_id,
            resource_type="mcp_server",
            visibility=visibility,
            resource={
                "server_type": server_type,
                "connection": connection,
                "visibility": visibility,
            },
            scanner=scanner,
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
        return self._semantic_review_with_policy_signals(
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
            scanner=scanner,
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
        if mcp_server_id is None:
            scanner.add("high", "mcp_credential.workspace_wide_scope")
        else:
            scanner.add("medium", "mcp_credential.server_scope")
        if has_secret_payload:
            scanner.add("high", "mcp_credential.hosted_secret")
        return self._semantic_review_with_policy_signals(
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
            scanner=scanner,
        )

    def review_plugin(
        self, *, workspace_id: UUID | None, visibility: str, name: str, manifest: dict[str, object]
    ) -> ResourceReview:
        scanner = ReviewScanner()
        scanner.scan_text("visibility", visibility)
        scanner.scan_text("name", name)
        scanner.scan_mapping("manifest", manifest)
        if visibility == "public":
            scanner.add("medium", "plugin.public_visibility")
        permissions = _list_from_manifest(manifest, "permissions")
        for permission in permissions:
            scanner.scan_text("permission", permission)
        return self._semantic_review_with_policy_signals(
            workspace_id=workspace_id,
            resource_type="plugin",
            visibility=visibility,
            resource={"visibility": visibility, "name": name, "manifest": manifest},
            scanner=scanner,
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
        scanner.scan_mapping("arguments", arguments)
        scanner.scan_mapping("context", context)
        if _has_sensitive_keys(arguments):
            scanner.add("high", "tool_execution.arguments.contains_sensitive_keys")
        return self._semantic_review_with_policy_signals(
            workspace_id=workspace_id,
            resource_type="tool_execution",
            visibility="public",
            resource={
                "tool_kind": tool_kind,
                "tool_name": tool_name,
                "arguments": redact_sensitive_payload(arguments),
                "context": redact_sensitive_payload(context),
                "static_signals": redact_sensitive_payload(static_signals),
            },
            scanner=scanner,
        )

    def request_resource_review(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        approval_type: str,
        target_type: str,
        target_id: UUID,
        target_name: str,
        review: ResourceReview,
        snapshot: dict[str, object],
    ) -> Approval:
        approval = ApprovalService(self._session).create_approval(
            workspace_id=workspace_id,
            task_id=None,
            agent_run_id=None,
            requested_by_agent_profile_id=None,
            approval_type=approval_type,
            risk_level=review.risk_level,
            payload={
                "kind": "resource_review",
                "action": "activate",
                "target_type": target_type,
                "target_id": str(target_id),
                "target_name": target_name,
                "review": {
                    "required": review.required,
                    "risk_level": review.risk_level,
                    "reasons": list(review.reasons),
                    "signals": dict(review.signals),
                },
                "snapshot": redact_sensitive_payload(snapshot),
            },
        )
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="resource_review.requested",
                target_type=target_type,
                target_id=target_id,
                metadata={
                    "approval_id": str(approval.id),
                    "approval_type": approval_type,
                    "risk_level": review.risk_level,
                    "reasons": list(review.reasons),
                },
            )
        return approval

    def _semantic_review_with_policy_signals(
        self,
        *,
        workspace_id: UUID | None,
        resource_type: str,
        visibility: str,
        resource: dict[str, object],
        scanner: ReviewScanner,
    ) -> ResourceReview:
        policy_review = scanner.result(reviewer="policy_guardrail")
        if workspace_id is not None and (
            not self._review_settings().enabled(workspace_id, resource_type, visibility)
        ):
            return review_skipped(policy_review, resource_type=resource_type, visibility=visibility)
        if workspace_id is None:
            return llm_unavailable_review(
                policy_review, ValueError("Workspace id is required for semantic resource review")
            )
        if self._settings is None:
            return llm_unavailable_review(
                policy_review, ValueError("Settings are required for semantic resource review")
            )
        review_config = self._review_settings().semantic_config(workspace_id)
        try:
            provider = self._model_provider_service().resolve_for_review(
                workspace_id=workspace_id,
                credential_id=review_config.credential_id,
                review_model=review_config.model,
            )
            llm_review = LlmResourceReviewer().review(
                provider=provider,
                resource_type=resource_type,
                resource=resource,
                static_signals=policy_review.signals,
                timeout_seconds=review_config.timeout_seconds,
            )
        except ModelProviderUnavailableError as exc:
            return llm_unavailable_review(policy_review, exc)
        except (ValueError, RuntimeError, OSError) as exc:
            return llm_unavailable_review(policy_review, exc)
        merged = merge_policy_and_llm_reviews(policy_review, llm_review)
        return merged

    def _review_settings(self) -> ResourceReviewSettings:
        return ResourceReviewSettings(self._session)

    def _model_provider_service(self) -> ModelProviderCredentialService:
        if self._settings is None:
            raise ModelProviderUnavailableError("Review settings are unavailable")
        return ModelProviderCredentialService(
            self._session,
            SecretEncryptionService(
                secret=self._settings.credential_encryption_secret,
                key_id=self._settings.credential_encryption_key_id,
                previous_secrets=self._settings.credential_encryption_previous_secrets,
            ),
        )
