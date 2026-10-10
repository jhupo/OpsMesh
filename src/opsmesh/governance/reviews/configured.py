from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.agents.providers.resolution import ModelProviderResolutionService
from opsmesh.governance.audit.service import AuditService
from opsmesh.governance.reviews.approval_config import (
    ActionKind,
    ApprovalConfiguration,
    ApprovalRule,
    approval_configuration,
)
from opsmesh.governance.reviews.command_matching import command_invocations
from opsmesh.governance.reviews.llm import LlmResourceReviewer
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task
from opsmesh.shared.config import Settings
from opsmesh.shared.security.redaction import redact_sensitive_payload
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.shared.utils import payload_hash
from opsmesh.workspaces.management.models import Workspace


@dataclass(frozen=True)
class ConfiguredReview:
    required: bool
    blocked: bool
    risk_level: str
    reasons: list[str]
    signals: dict[str, object]


class ConfiguredApprovalService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings

    def review(
        self,
        *,
        workspace_id: UUID,
        action: ActionKind,
        name: str,
        arguments: dict[str, object],
        context: dict[str, object],
        configured_review: bool = False,
        force_human: bool = False,
    ) -> ConfiguredReview:
        workspace = self.session.get(Workspace, workspace_id)
        if workspace is None:
            raise ValueError("Approval workspace not found")
        config = approval_configuration(workspace.settings)
        context = dict(context)
        run_id = context.get("agent_run_id") or context.get("run_id")
        if isinstance(run_id, str):
            run = self.session.get(AgentRun, UUID(run_id))
            if run is None or run.workspace_id != workspace_id:
                raise ValueError("Approval run outside workspace")
            if run.task_id is not None:
                task = self.session.get(Task, run.task_id)
                if task is not None and task.workspace_id == workspace_id:
                    context["task_title"] = task.title
                    context["task_description"] = task.description

        invocations = [arguments]
        opaque = False
        if action == "runtime_command" and any(r.action == action for r in config.rules):
            command = arguments.get("command")
            if isinstance(command, list):
                parsed = command_invocations(command)
                opaque = parsed is None
                if parsed is not None:
                    invocations = [{"command": item} for item in parsed]
        matched = []
        decisions = []
        for invocation in invocations:
            invocation_matches = []
            command_value = invocation.get("command")
            invocation_name = (
                str(command_value[0])
                if action == "runtime_command" and isinstance(command_value, list) and command_value
                else name
            )
            for rule in config.rules:
                if _matches(rule, action, invocation_name, invocation, context):
                    invocation_matches.append(rule)
                    if rule not in matched:
                        matched.append(rule)
            decisions.extend([r.decision for r in invocation_matches] or [config.default])
        # Every compound-command component must satisfy policy independently.
        if opaque:
            decisions.append(config.opaque_commands)
        if configured_review:
            decisions.append("review")
        decision = max(decisions, key={"allow": 0, "review": 1, "deny": 2}.__getitem__)
        signals: dict[str, object] = {"matched_rules": [r.id for r in matched], "action": action}
        if decision != "review":
            return ConfiguredReview(
                False, decision == "deny", "low", ["configured_policy." + decision], signals
            )
        reviewers = [r.reviewer or config.reviewer for r in matched if r.decision == "review"]
        reviewer = "human" if "human" in reviewers else config.reviewer
        if reviewers and all(r == "model" for r in reviewers):
            reviewer = "model"
        if reviewer == "human" or force_human:
            return ConfiguredReview(True, False, "low", ["configured_policy.human"], signals)
        return self._model_review(config, workspace_id, action, name, arguments, context, signals)

    def _model_review(
        self,
        config: ApprovalConfiguration,
        workspace_id: UUID,
        action: str,
        name: str,
        arguments: dict[str, object],
        context: dict[str, object],
        signals: dict[str, object],
    ) -> ConfiguredReview:
        model = config.model
        if model is None:
            raise ValueError("Approval model is not configured")
        signals = {
            **signals,
            "prompt_sha256": sha256(model.instructions.encode()).hexdigest(),
            "reviewer": "model",
            "model": model.model,
            "model_provider_credential_id": str(model.model_provider_credential_id),
        }
        try:
            if self.settings is None:
                raise ValueError("Approval runtime settings unavailable")
            provider = ModelProviderResolutionService(
                self.session,
                SecretEncryptionService(
                    secret=self.settings.credential_encryption_secret,
                    key_id=self.settings.credential_encryption_key_id,
                    previous_secrets=self.settings.credential_encryption_previous_secrets,
                ),
            ).resolve_for_review(
                workspace_id=workspace_id,
                credential_id=model.model_provider_credential_id,
                review_model=model.model,
            )
            result = LlmResourceReviewer().review(
                provider=provider,
                resource_type=action,
                resource={
                    "name": name,
                    "arguments": redact_sensitive_payload(arguments),
                    "context": redact_sensitive_payload(context),
                },
                static_signals=signals,
                timeout_seconds=model.timeout_seconds,
                max_output_tokens=model.max_output_tokens,
                instructions=model.instructions,
            )
            verdict = result.signals.get("verdict")
            if verdict not in {"approve", "reject", "needs_human"}:
                raise ValueError("Invalid approval model verdict")
            signals.update(result.signals)
            review = ConfiguredReview(
                verdict in {"needs_human"},
                verdict == "reject",
                result.risk_level,
                result.reasons,
                signals,
            )
        except Exception as exc:
            signals["review_error_type"] = type(exc).__name__
            signals["verdict"] = "unavailable"
            review = ConfiguredReview(
                model.on_error == "human",
                model.on_error == "deny",
                "low",
                ["approval_model.unavailable"],
                signals,
            )
        evidence = redact_sensitive_payload(
            {
                "reviewer": "model",
                "action": action,
                "name": name,
                "action_fingerprint": payload_hash({"arguments": arguments, "context": context}),
                "policy_fingerprint": payload_hash(config.model_dump(mode="json")),
                "reasons": review.reasons,
                "signals": review.signals,
            }
        )
        if signals.get("verdict") in {"approve", "reject"}:
            run_id = context.get("agent_run_id") or context.get("run_id")
            approval = Approval(
                workspace_id=workspace_id,
                task_id=None,
                agent_run_id=UUID(run_id) if isinstance(run_id, str) else None,
                requested_by_agent_profile_id=None,
                approval_type=f"automatic.{action}",
                risk_level=review.risk_level,
                payload=evidence,
                created_at=datetime.now(UTC),
                status="approved" if signals["verdict"] == "approve" else "rejected",
                decided_at=datetime.now(UTC),
                decision_reason="approval_model." + str(signals["verdict"]),
            )
            self.session.add(approval)
        # Store only the decision/evidence, never credentials or raw arguments.
        AuditService(self.session).record_system_action(
            workspace_id=workspace_id,
            action="approval.model_reviewed",
            target_type=action,
            target_id=name,
            actor_id="opsmesh.approval_model",
            metadata={
                "required": review.required,
                "blocked": review.blocked,
                "reasons": review.reasons,
                "signals": evidence,
            },
        )
        return review


def _matches(
    rule: ApprovalRule,
    action: str,
    name: str,
    arguments: dict[str, object],
    context: dict[str, object],
) -> bool:
    if rule.action != action or (rule.name is not None and rule.name != name):
        return False
    if rule.server_id is not None and str(rule.server_id) != context.get("mcp_server_id"):
        return False
    if rule.visibility is not None and rule.visibility != context.get("visibility"):
        return False
    if rule.command_prefix is not None:
        command = arguments.get("command")
        if (
            not isinstance(command, list)
            or command[: len(rule.command_prefix)] != rule.command_prefix
        ):
            return False
    return True
