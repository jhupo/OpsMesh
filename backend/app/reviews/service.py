from __future__ import annotations

from dataclasses import dataclass
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
from backend.app.reviews.constants import (
    DEFAULT_RESOURCE_REVIEW_MODEL,
    RESOURCE_REVIEW_SETTINGS_KEY,
    SEMANTIC_REVIEW_SETTINGS_KEY,
)
from backend.app.reviews.llm import LlmResourceReviewer, LlmReviewResult
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.redaction import redact_sensitive_payload
from backend.app.workspaces.models import Workspace

_HIGH_RISK_LEVELS = {"high", "critical"}
_REVIEW_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_HIGH_RISK_TERMS = {
    "bypass",
    "credential",
    "delete",
    "payment",
    "production",
    "root",
    "secret",
    "shell",
    "sudo",
    "token",
}
_DANGEROUS_WORDS = {
    "admin",
    "approve",
    "approval",
    "bypass",
    "credential",
    "delete",
    "deploy",
    "exec",
    "filesystem",
    "network",
    "payment",
    "production",
    "root",
    "secret",
    "shell",
    "sudo",
    "token",
    "write",
}


@dataclass(frozen=True)
class ResourceReview:
    required: bool
    risk_level: str
    reasons: list[str]
    signals: dict[str, object]


class ResourceReviewService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings

    def review_agent_profile(
        self,
        *,
        workspace_id: UUID | None,
        name: str,
        role: str,
        instructions: str,
        capabilities: dict[str, object],
        skills: dict[str, object],
        tool_policy: dict[str, object],
        runtime_policy: dict[str, object],
        approval_policy: dict[str, object],
    ) -> ResourceReview:
        scanner = _ReviewScanner()
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
        scanner = _ReviewScanner()
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
        scanner = _ReviewScanner()
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
        scanner = _ReviewScanner()
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
        tool_name: str,
        requires_approval: bool,
        risk_level: str,
        policy: dict[str, object],
    ) -> ResourceReview:
        scanner = _ReviewScanner()
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
            resource={
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
        mcp_server_id: UUID | None,
        name: str,
        provider: str,
        external_ref: str,
        scopes: list[str],
        has_secret_payload: bool,
    ) -> ResourceReview:
        scanner = _ReviewScanner()
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
            resource={
                "mcp_server_id": str(mcp_server_id) if mcp_server_id is not None else None,
                "name": name,
                "provider": provider,
                "external_ref": external_ref,
                "scopes": scopes,
                "has_secret_payload": has_secret_payload,
            },
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
        scanner = _ReviewScanner()
        scanner.scan_text("tool_kind", tool_kind)
        scanner.scan_mapping("arguments", arguments)
        scanner.scan_mapping("context", context)
        if _has_sensitive_keys(arguments):
            scanner.add("high", "tool_execution.arguments.contains_sensitive_keys")
        return self._semantic_review_with_policy_signals(
            workspace_id=workspace_id,
            resource_type="tool_execution",
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
        resource: dict[str, object],
        scanner: _ReviewScanner,
    ) -> ResourceReview:
        policy_review = scanner.result(reviewer="policy_guardrail")
        if workspace_id is None:
            return _llm_unavailable_review(
                policy_review,
                ValueError("Workspace id is required for semantic resource review"),
            )
        if self._settings is None:
            return _llm_unavailable_review(
                policy_review,
                ValueError("Settings are required for semantic resource review"),
            )
        review_config = self._review_config(workspace_id)
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
            return _llm_unavailable_review(policy_review, exc)
        except (ValueError, RuntimeError, OSError) as exc:
            return _llm_unavailable_review(policy_review, exc)
        merged = _merge_policy_and_llm_reviews(policy_review, llm_review)
        return merged

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

    def _review_config(self, workspace_id: UUID) -> _ResourceReviewConfig:
        workspace = self._session.get(Workspace, workspace_id)
        settings = workspace.settings if workspace is not None else {}
        raw = settings.get(RESOURCE_REVIEW_SETTINGS_KEY) if isinstance(settings, dict) else None
        resource_review = raw if isinstance(raw, dict) else {}
        raw_semantic = resource_review.get(SEMANTIC_REVIEW_SETTINGS_KEY)
        config = raw_semantic if isinstance(raw_semantic, dict) else {}
        if config.get("enabled") is False:
            raise ModelProviderUnavailableError("Semantic resource review is disabled")
        return _ResourceReviewConfig(
            credential_id=_uuid_or_none(config.get("model_provider_credential_id")),
            model=_review_model(config.get("model")),
            timeout_seconds=_review_timeout_seconds(config.get("timeout_seconds")),
        )


class _ReviewScanner:
    def __init__(self) -> None:
        self._risk_level = "low"
        self._reasons: list[str] = []
        self._signals: dict[str, object] = {"matched_terms": []}

    def add(self, risk_level: str, reason: str) -> None:
        normalized = _normalize_risk(risk_level)
        if _REVIEW_RISK_ORDER[normalized] > _REVIEW_RISK_ORDER[self._risk_level]:
            self._risk_level = normalized
        if reason not in self._reasons:
            self._reasons.append(reason)

    def scan_text(self, field: str, value: object) -> None:
        if not isinstance(value, str) or not value:
            return
        lowered = value.lower()
        matches = sorted(term for term in _DANGEROUS_WORDS if term in lowered)
        if not matches:
            return
        matched_terms = self._signals.setdefault("matched_terms", [])
        if isinstance(matched_terms, list):
            for match in matches:
                entry = {"field": field, "term": match}
                if entry not in matched_terms:
                    matched_terms.append(entry)
        risk_level = (
            "medium"
            if field.startswith(("connection.", "manifest.", "policy."))
            else "high"
            if any(match in _HIGH_RISK_TERMS for match in matches)
            else "medium"
        )
        self.add(risk_level, f"{field}.contains_sensitive_terms")

    def scan_mapping(self, field: str, value: object) -> None:
        if not isinstance(value, dict):
            return
        for path, item in _walk_mapping(value, field):
            if isinstance(item, str):
                self.scan_text(path, item)
            elif isinstance(item, bool) and item and _path_has_dangerous_term(path):
                self.add("medium", f"{path}.enabled")

    def result(self, *, reviewer: str) -> ResourceReview:
        reasons = list(self._reasons)
        required = self._risk_level in _HIGH_RISK_LEVELS
        if not reasons:
            reasons.append("resource.low_risk")
        signals = dict(self._signals)
        signals["reviewer"] = reviewer
        return ResourceReview(
            required=required,
            risk_level=self._risk_level,
            reasons=reasons,
            signals=signals,
        )


@dataclass(frozen=True)
class _ResourceReviewConfig:
    credential_id: UUID | None
    model: str
    timeout_seconds: float


def _merge_policy_and_llm_reviews(
    policy_review: ResourceReview,
    llm_review: LlmReviewResult,
) -> ResourceReview:
    llm_required = bool(llm_review.required)
    llm_risk = _normalize_risk(llm_review.risk_level)
    llm_reasons = list(llm_review.reasons)
    llm_signals = dict(llm_review.signals)
    required = llm_required or llm_risk in _HIGH_RISK_LEVELS
    reasons = [*llm_reasons, *policy_review.reasons]
    deduped_reasons = list(dict.fromkeys(reasons))[:12]
    signals = {
        **llm_signals,
        "policy_guardrail": {
            "risk_level": policy_review.risk_level,
            "required": policy_review.required,
            "reasons": policy_review.reasons,
            "signals": policy_review.signals,
        },
    }
    return ResourceReview(
        required=required,
        risk_level=llm_risk,
        reasons=deduped_reasons or ["llm_review.approved"],
        signals=signals,
    )


def _llm_unavailable_review(
    policy_review: ResourceReview,
    exc: Exception,
) -> ResourceReview:
    signals = dict(policy_review.signals)
    signals["reviewer"] = "llm_unavailable_fail_closed"
    signals["semantic_review_error"] = type(exc).__name__
    return ResourceReview(
        required=True,
        risk_level=_max_risk(policy_review.risk_level, "high"),
        reasons=list(
            dict.fromkeys(["llm_review.unavailable_requires_admin", *policy_review.reasons])
        )[:12],
        signals=signals,
    )


def _max_risk(left: str, right: str) -> str:
    left_risk = _normalize_risk(left)
    right_risk = _normalize_risk(right)
    return (
        left_risk
        if _REVIEW_RISK_ORDER[left_risk] >= _REVIEW_RISK_ORDER[right_risk]
        else right_risk
    )


def _walk_mapping(value: dict[str, object], prefix: str) -> list[tuple[str, object]]:
    rows: list[tuple[str, object]] = []
    for key, item in value.items():
        path = f"{prefix}.{key}"
        rows.append((path, item))
        if isinstance(item, dict):
            rows.extend(_walk_mapping(item, path))
        elif isinstance(item, list):
            for index, child in enumerate(item[:50]):
                child_path = f"{path}[{index}]"
                rows.append((child_path, child))
                if isinstance(child, dict):
                    rows.extend(_walk_mapping(child, child_path))
    return rows


def _normalize_risk(value: object) -> str:
    raw = str(value or "low").lower().strip()
    return raw if raw in _REVIEW_RISK_ORDER else "low"


def _review_model(value: object) -> str:
    model = value.strip() if isinstance(value, str) else ""
    return model or DEFAULT_RESOURCE_REVIEW_MODEL


def _review_timeout_seconds(value: object) -> float:
    if isinstance(value, bool):
        return 20.0
    if isinstance(value, int | float) and 1 <= value <= 120:
        return float(value)
    return 20.0


def _uuid_or_none(value: object) -> UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise ValueError("Review model provider credential id must be a UUID")


def _policy_mode(policy: dict[str, object]) -> str:
    return str(policy.get("mode") or policy.get("tool_access") or "").lower().strip()


def _path_has_dangerous_term(path: str) -> bool:
    lowered = path.lower()
    return any(term in lowered for term in _DANGEROUS_WORDS)


def _list_from_manifest(manifest: dict[str, object], key: str) -> list[str]:
    value = manifest.get(key)
    if not isinstance(value, list):
        value = manifest.get("tools")
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _connection_has_external_url(connection: dict[str, object]) -> bool:
    for _, item in _walk_mapping(connection, "connection"):
        if isinstance(item, str) and (
            item.startswith("http://")
            or item.startswith("https://")
            or item.startswith("ws://")
            or item.startswith("wss://")
        ):
            return True
    return False


def _has_sensitive_keys(value: dict[str, object]) -> bool:
    for key, item in value.items():
        if isinstance(key, str) and any(term in key.lower() for term in _DANGEROUS_WORDS):
            return True
        if isinstance(item, dict) and _has_sensitive_keys(item):
            return True
    return False
