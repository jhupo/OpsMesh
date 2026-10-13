from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.providers.metadata import budget_metadata_summary
from opsmesh.agents.providers.model_api import (
    model_api_for_provider,
    model_api_options_for_provider,
)
from opsmesh.agents.providers.models import ModelProviderCredential
from opsmesh.agents.providers.policy import (
    credential_is_selectable,
    credential_not_selectable_reasons,
)
from opsmesh.agents.providers.views import agent_model_provider_summary
from opsmesh.orchestration.runs.models import AgentRun, RunEvent
from opsmesh.orchestration.runs.statuses import ACTIVE_RUN_STATUSES
from opsmesh.runtime.operations.contracts.providers import (
    ModelProviderOperationsAgentResponse,
    ModelProviderOperationsCredentialResponse,
    ModelProviderOperationsResponse,
    ModelProviderOperationsRunResponse,
)
from opsmesh.shared.security.redaction import redact_sensitive_payload, redact_sensitive_text
from opsmesh.shared.utils import dict_or_empty, int_or_zero, string_list, uuid_or_none


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
        summary = self._summary(agent_payloads, run_payloads, credentials)
        suggested_actions = self._suggested_actions(agent_payloads)
        return ModelProviderOperationsResponse(
            workspace_id=workspace_id,
            generated_at=datetime.now(UTC),
            summary=summary,
            credentials=[self._credential_payload(credential) for credential in credentials],
            agents=agent_payloads,
            runs=run_payloads,
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
                RunEvent.event_type == "model.request_failed",
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
            reasons=string_list(summary.get("reasons")),
            warnings=string_list(summary.get("warnings")),
            failure={
                "count": int_or_zero(summary.get("failure_count")),
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
        return dict_or_empty(authorization.get("model_provider"))

    def _summary(
        self,
        agents: list[ModelProviderOperationsAgentResponse],
        runs: list[ModelProviderOperationsRunResponse],
        credentials: list[ModelProviderCredential],
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
        }

    def _suggested_actions(
        self,
        agents: list[ModelProviderOperationsAgentResponse],
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
        return actions


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _redacted_string(value: object) -> str | None:
    return redact_sensitive_text(value) if isinstance(value, str) else None
