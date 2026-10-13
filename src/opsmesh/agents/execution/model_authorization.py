"""Product model authorization, resolved only at an Activity boundary."""

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.agents.execution.providers.openai.contracts import AgentModelReference
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.providers.contracts import ResolvedModelProvider
from opsmesh.agents.providers.resolution import ModelProviderResolver
from opsmesh.governance.costs.service import CostAccountingService
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.queries import authorization_snapshot_for_run
from opsmesh.resources.memory.policy import agent_memory_policy
from opsmesh.shared.security.secrets import SecretEncryptionService


class AuthorizedModelResolver:
    def __init__(self, sessions: Callable[[], Session], secrets: SecretEncryptionService) -> None:
        self._sessions = sessions
        self._secrets = secrets

    def resolve(self, reference: AgentModelReference) -> ResolvedModelProvider:
        with self._sessions() as session:
            ExecutionIdentityService(session).for_run(reference.workspace_id, reference.run_id)
            profile = session.scalar(
                select(AgentProfile.id).where(
                    AgentProfile.id == reference.agent_profile_id,
                    AgentProfile.workspace_id == reference.workspace_id,
                    AgentProfile.status == "active",
                    AgentProfile.archived_at.is_(None),
                )
            )
            if profile is None:
                raise ResourceAccessDeniedError()
            run = session.scalar(
                select(AgentRun).where(
                    AgentRun.id == reference.run_id,
                    AgentRun.workspace_id == reference.workspace_id,
                    AgentRun.status == "running",
                )
            )
            if run is None or not _authorized_model(reference, run):
                raise ResourceAccessDeniedError()
            resolved = ModelProviderResolver(session, self._secrets).resolve_for_agent(
                workspace_id=reference.workspace_id,
                agent_credential_id=reference.credential_id,
                agent_model=reference.model,
            )
            if resolved.provider is None:
                raise ResourceAccessDeniedError()
            CostAccountingService(session).assert_budget_available(
                reference.workspace_id, provider=resolved.provider, model=reference.model
            )
            return resolved


def _authorized_model(reference: AgentModelReference, run: AgentRun) -> bool:
    snapshot = authorization_snapshot_for_run(run)
    if run.agent_profile_id == reference.agent_profile_id:
        provider = snapshot.get("model_provider")
        if _matches_provider(reference, provider) and _allows_purpose(reference, snapshot):
            return True
    targets: list[object] = [snapshot.get("agent_tools"), snapshot.get("handoffs")]
    while targets:
        item = targets.pop()
        if isinstance(item, list):
            targets.extend(item)
        elif isinstance(item, dict):
            target = item.get("target")
            if (
                isinstance(target, dict)
                and target.get("profile_id") == str(reference.agent_profile_id)
                and _matches_provider(reference, item.get("model_provider"))
                and _allows_purpose(reference, item)
            ):
                return True
            targets.extend((item.get("agent_tools"), item.get("handoffs")))
    return False


def _allows_purpose(reference: AgentModelReference, snapshot: dict[str, object]) -> bool:
    if reference.purpose == "agent":
        return True
    policy = agent_memory_policy(snapshot.get("memory_policy")).sdk_memory
    return policy.enabled and policy.generate


def _matches_provider(reference: AgentModelReference, value: object) -> bool:
    return (
        isinstance(value, dict)
        and value.get("credential_id") == str(reference.credential_id)
        and value.get("selected_model") == reference.model
        and value.get("model_api") == reference.model_api
    )
