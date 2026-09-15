from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security.redaction import redact_sensitive_payload, redact_sensitive_text
from backend.app.core.utils import dict_or_empty, uuid_or_none
from backend.app.domains.agents.profiles.models import AgentProfile
from backend.app.domains.agents.providers.metadata import (
    budget_metadata_summary,
)
from backend.app.domains.agents.providers.model_api import (
    canonical_model_api,
    model_api_for_provider,
    model_api_options_for_provider,
)
from backend.app.domains.agents.providers.models import ModelProviderCredential
from backend.app.domains.agents.providers.policy import (
    credential_is_selectable,
    credential_not_selectable_reasons,
)
from backend.app.domains.agents.providers.views import (
    agent_model_provider_summary,
)
from backend.app.domains.orchestration.runs.models import AgentRun, RunEvent
from backend.app.domains.orchestration.workflows.statuses import ACTIVE_RUN_STATUSES
from backend.app.domains.workspace.tenants.models import Workspace
from backend.app.runtime.operations.contracts.providers import (
    ModelProviderOperationsAgentResponse,
    ModelProviderOperationsCredentialResponse,
    ModelProviderOperationsResponse,
    ModelProviderOperationsRunResponse,
)


class ModelProviderOperationsService:
    """Workspace-scoped provider/model diagnostics for operators.

    This service is intentionally read-only.  It composes the same resolution and policy
    contracts used by run scheduling, so the operations view cannot report a different provider
    or protocol than the worker will use.  Secrets and arbitrary vendor payloads are projected
    through the redaction boundary before they reach the API schema.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def payload(
        self,
        workspace_id: UUID,
        *,
        run_limit: int = 50,
    ) -> ModelProviderOperationsResponse:
        credentials = list(
            self._session.scalars(
                select(ModelProviderCredential)
                .where(ModelProviderCredential.workspace_id == workspace_id)
                .order_by(ModelProviderCredential.name.asc(), ModelProviderCredential.id.asc())
            )
        )
        credential_by_id = {credential.id: credential for credential in credentials}
        agents = list(
            self._session.scalars(
                select(AgentProfile)
                .where(AgentProfile.workspace_id == workspace_id)
                .order_by(AgentProfile.name.asc(), AgentProfile.id.asc())
            )
        )
        runs = self._runs(workspace_id, run_limit)
        events = self._run_events(workspace_id, [run.id for run in runs])
        agent_payloads = [self._agent_payload(agent) for agent in agents]
        run_payloads = [self._run_payload(run, events.get(run.id, [])) for run in runs]
        fallback = self._fallback_payload(workspace_id, credential_by_id, run_payloads)
        summary = self._summary(agent_payloads, run_payloads, credentials, fallback)
        suggested_actions = self._suggested_actions(agent_payloads, fallback)
        return ModelProviderOperationsResponse(
            workspace_id=workspace_id,
            generated_at=datetime.now(UTC),
            summary=summary,
            credentials=[self._credential_payload(credential) for credential in credentials],
            agents=agent_payloads,
            runs=run_payloads,
            fallback=fallback,
            suggested_actions=suggested_actions,
        )

    def _runs(self, workspace_id: UUID, limit: int) -> list[AgentRun]:
        bounded_limit = max(1, min(limit, 200))
        return list(
            self._session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.status.in_(ACTIVE_RUN_STATUSES),
                )
                .order_by(AgentRun.updated_at.desc(), AgentRun.created_at.desc())
                .limit(bounded_limit)
            )
        )

    def _run_events(self, workspace_id: UUID, run_ids: list[UUID]) -> dict[UUID, list[RunEvent]]:
        if not run_ids:
            return {}
        events = self._session.scalars(
            select(RunEvent)
            .where(
                RunEvent.workspace_id == workspace_id,
                RunEvent.agent_run_id.in_(run_ids),
                RunEvent.event_type.in_(
                    {
                        "model_provider.fallback_selected",
                        "model_provider.fallback_unavailable",
                        "model.request_failed",
                    }
                ),
            )
            .order_by(RunEvent.created_at.asc(), RunEvent.sequence.asc())
        )
        grouped: dict[UUID, list[RunEvent]] = {}
        for event in events:
            grouped.setdefault(event.agent_run_id, []).append(event)
        return grouped

    def _credential_payload(
        self,
        credential: ModelProviderCredential,
    ) -> ModelProviderOperationsCredentialResponse:
        return ModelProviderOperationsCredentialResponse(
            id=credential.id,
            name=credential.name,
            provider=credential.provider,
            default_model=credential.default_model,
            model_api=model_api_for_provider(credential.provider, credential.budget_metadata),
            model_apis=list(model_api_options_for_provider(credential.provider)),
            status=credential.status,
            health_status=credential.health_status,
            failure_count=credential.failure_count,
            last_failure_code=credential.last_failure_code,
            last_failure_message=(
                redact_sensitive_text(credential.last_failure_message)
                if credential.last_failure_message is not None
                else None
            ),
            selectable=credential_is_selectable(credential),
            not_selectable_reasons=credential_not_selectable_reasons(credential),
            budget=budget_metadata_summary(credential.budget_metadata),
            provenance={
                "credential_reference": f"model_provider_credentials:{credential.id}",
                "is_default": credential.is_default,
                "base_url_configured": bool(credential.base_url),
                "api_key_fingerprint": credential.api_key_fingerprint,
            },
        )

    def _agent_payload(self, agent: AgentProfile) -> ModelProviderOperationsAgentResponse:
        summary = agent_model_provider_summary(self._session, agent)
        credential_id = uuid_or_none(summary.get("credential_id"))
        credential = (
            self._session.scalar(
                select(ModelProviderCredential).where(
                    ModelProviderCredential.workspace_id == agent.workspace_id,
                    ModelProviderCredential.id == credential_id,
                )
            )
            if credential_id is not None
            else None
        )
        return ModelProviderOperationsAgentResponse(
            id=agent.id,
            name=agent.name,
            role=agent.role,
            status=agent.status,
            model=str(summary.get("selected_model") or agent.model),
            provider=_string_or_none(summary.get("provider")),
            protocol=_string_or_none(summary.get("model_api")),
            model_api=_string_or_none(summary.get("model_api")),
            credential_id=credential_id,
            readiness_status=str(summary.get("readiness_status") or "blocked"),
            reasons=_string_list(summary.get("reasons")),
            warnings=_string_list(summary.get("warnings")),
            failure={
                "count": _int_or_zero(summary.get("failure_count")),
                "health_status": _string_or_none(summary.get("credential_health_status")),
                "last_failure_code": _string_or_none(summary.get("last_failure_code")),
                "last_failure_message": _redacted_string(summary.get("last_failure_message")),
                "last_failure_at": summary.get("last_failure_at"),
            },
            budget=budget_metadata_summary(
                credential.budget_metadata
                if credential is not None
                else summary.get("budget_metadata")
            ),
            provenance={
                "source": summary.get("source"),
                "credential_reference": summary.get("credential_reference"),
                "base_url_host": summary.get("base_url_host"),
                "model_capability": summary.get("model_capability"),
            },
        )

    def _run_payload(
        self,
        run: AgentRun,
        events: list[RunEvent],
    ) -> ModelProviderOperationsRunResponse:
        provider = self._run_provider_snapshot(run)
        fallback_selected = any(
            event.event_type == "model_provider.fallback_selected" for event in events
        )
        fallback_unavailable = any(
            event.event_type == "model_provider.fallback_unavailable" for event in events
        )
        fallback_event = next(
            (
                event
                for event in reversed(events)
                if event.event_type
                in {
                    "model_provider.fallback_selected",
                    "model_provider.fallback_unavailable",
                }
            ),
            None,
        )
        failure_event = next(
            (event for event in reversed(events) if event.event_type == "model.request_failed"),
            None,
        )
        return ModelProviderOperationsRunResponse(
            run_id=run.id,
            task_id=run.task_id,
            task_step_id=run.task_step_id,
            agent_profile_id=run.agent_profile_id,
            status=run.status,
            model=(
                _string_or_none(provider.get("model"))
                or _string_or_none(provider.get("selected_model"))
                or run.model
            ),
            provider=_string_or_none(provider.get("provider")),
            protocol=_string_or_none(provider.get("model_api")),
            credential_id=uuid_or_none(provider.get("credential_id")),
            provider_snapshot_source=(
                "frozen_run_snapshot" if provider else "run_metadata_unavailable"
            ),
            fallback_status=(
                "selected"
                if fallback_selected
                else "unavailable"
                if fallback_unavailable
                else "not_used"
            ),
            fallback=(
                redact_sensitive_payload(dict_or_empty(fallback_event.event_metadata))
                if fallback_event is not None
                else {}
            ),
            failure=(
                redact_sensitive_payload(
                    dict_or_empty(dict_or_empty(failure_event.event_metadata).get("reason"))
                )
                if failure_event is not None
                else {}
            ),
            provenance={
                "authorization_snapshot": bool(self._run_provider_snapshot(run)),
                "source": provider.get("source"),
            },
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    def _run_provider_snapshot(self, run: AgentRun) -> dict[str, object]:
        input_payload = dict_or_empty(run.input)
        authorization = dict_or_empty(input_payload.get("authorization_snapshot"))
        snapshot = dict_or_empty(authorization.get("model_provider"))
        if snapshot:
            return snapshot
        return dict_or_empty(input_payload.get("model_provider"))

    def _fallback_payload(
        self,
        workspace_id: UUID,
        credential_by_id: dict[UUID, ModelProviderCredential],
        runs: list[ModelProviderOperationsRunResponse],
    ) -> dict[str, object]:
        workspace = self._session.get(Workspace, workspace_id)
        settings = workspace.settings if workspace is not None else {}
        raw = dict_or_empty(settings).get("model_provider_fallback")
        policy = dict_or_empty(raw)
        candidates: list[dict[str, object]] = []
        raw_candidates = policy.get("candidates")
        for candidate in raw_candidates if isinstance(raw_candidates, list) else []:
            if not isinstance(candidate, dict):
                continue
            credential_id = uuid_or_none(candidate.get("credential_id"))
            credential = credential_by_id.get(credential_id) if credential_id is not None else None
            requested_model = _string_or_none(candidate.get("model"))
            requested_api = canonical_model_api(candidate.get("model_api"))
            reasons = credential_not_selectable_reasons(credential)
            if credential is None:
                reasons = [*reasons, "fallback_credential_not_found"]
            if requested_model is None:
                reasons = [*reasons, "fallback_model_missing"]
            if (
                requested_api is not None
                and credential is not None
                and requested_api not in model_api_options_for_provider(credential.provider)
            ):
                reasons = [*reasons, "fallback_model_api_unsupported"]
            candidates.append(
                {
                    "credential_id": credential_id,
                    "provider": credential.provider if credential is not None else None,
                    "model": requested_model,
                    "model_api": requested_api,
                    "selectable": not reasons,
                    "reasons": list(dict.fromkeys(reasons)),
                }
            )
        selected_count = sum(1 for run in runs if run.fallback_status == "selected")
        unavailable_count = sum(1 for run in runs if run.fallback_status == "unavailable")
        return redact_sensitive_payload(
            {
                "enabled": policy.get("enabled") is True,
                "retry_error_codes": _string_list(policy.get("retry_error_codes")),
                "candidates": candidates,
                "selected_run_count": selected_count,
                "unavailable_run_count": unavailable_count,
            }
        )

    def _summary(
        self,
        agents: list[ModelProviderOperationsAgentResponse],
        runs: list[ModelProviderOperationsRunResponse],
        credentials: list[ModelProviderCredential],
        fallback: dict[str, object],
    ) -> dict[str, object]:
        statuses = {
            status: sum(1 for agent in agents if agent.readiness_status == status)
            for status in ("ready", "degraded", "blocked")
        }
        return {
            "agent_count": len(agents),
            "ready_agent_count": statuses["ready"],
            "degraded_agent_count": statuses["degraded"],
            "blocked_agent_count": statuses["blocked"],
            "credential_count": len(credentials),
            "active_credential_count": sum(1 for item in credentials if item.status == "active"),
            "healthy_credential_count": sum(
                1 for item in credentials if item.health_status == "healthy"
            ),
            "active_run_count": len(runs),
            "fallback_enabled": fallback.get("enabled") is True,
            "fallback_selected_run_count": fallback.get("selected_run_count", 0),
            "fallback_unavailable_run_count": fallback.get("unavailable_run_count", 0),
        }

    def _suggested_actions(
        self,
        agents: list[ModelProviderOperationsAgentResponse],
        fallback: dict[str, object],
    ) -> list[dict[str, object]]:
        actions: list[dict[str, object]] = []
        for agent in agents:
            if agent.readiness_status == "ready":
                continue
            actions.append(
                {
                    "action": "review_agent_model_provider",
                    "priority": 100 if agent.readiness_status == "blocked" else 60,
                    "agent_profile_id": agent.id,
                    "reason": f"agent_model_provider_{agent.readiness_status}",
                    "reasons": [*agent.reasons, *agent.warnings],
                }
            )
        if fallback.get("enabled") is True and fallback.get("unavailable_run_count", 0):
            actions.append(
                {
                    "action": "review_model_provider_fallback",
                    "priority": 90,
                    "reason": "model_provider_fallback_unavailable",
                    "run_count": fallback.get("unavailable_run_count", 0),
                }
            )
        return actions


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_list(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _int_or_zero(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _redacted_string(value: object) -> str | None:
    return redact_sensitive_text(value) if isinstance(value, str) else None
