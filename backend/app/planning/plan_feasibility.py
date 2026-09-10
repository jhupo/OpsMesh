"""Validate that an admitted agent plan can execute against live workspace state."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import NoReturn
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.schemas.capabilities.catalog import EffectiveCapabilityCatalogResponse
from backend.app.capabilities.effective_catalog import EffectiveCapabilityCatalogService
from backend.app.core.errors import DomainError
from backend.app.costs.service import CostAccountingService, CostBudgetExceededError
from backend.app.model_providers.resolution import ModelProviderResolutionService
from backend.app.orchestration.resource_usage import merge_usage_max, positive_int_usage
from backend.app.orchestration.scheduler_policy import (
    SchedulerPolicyResolver,
    WorkspaceSchedulerPolicy,
)
from backend.app.planning.org_structure import normalize_role
from backend.app.planning.project_plan_validation import ProjectPlanValidationError
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceQuota
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workspaces.models import WorkspaceQuota

_ACTIVE_STATUS = "active"
_ACTIVE_RUNS_KEY = "active_runs"
_MAX_ESTIMATED_COST_USD = Decimal("1000000")


class PlanFeasibilityService:
    """Keep plan admission fail-closed when live authorization or capacity changed."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def validate(
        self,
        *,
        task: Task,
        plan: dict[str, object],
        planner_run: AgentRun | None = None,
    ) -> None:
        if planner_run is not None and (
            planner_run.workspace_id != task.workspace_id or planner_run.task_id != task.id
        ):
            _reject("plan_context_invalid")

        team = self._active_team(task)
        if team is None:
            _reject("plan_team_unavailable")
        snapshot = task.team_snapshot
        snapshot_team = self._validate_team_snapshot(team, snapshot)
        packages = _packages(plan)
        profile_ids = _assigned_profile_ids(packages)
        profiles = self._profiles(task.workspace_id, profile_ids)
        members = self._members(task.workspace_id, team.id, profile_ids)
        scheduler_policy = self._scheduler_policy(task.workspace_id, team)
        runtime_space_id = task.runtime_space_id or team.runtime_space_id
        runtime_space = self._runtime_space(task.workspace_id, runtime_space_id)
        catalogs: dict[UUID, EffectiveCapabilityCatalogResponse] = {}
        total_estimated_cost = Decimal("0")
        provider_keys: set[tuple[UUID, str, str]] = set()

        for package in packages:
            profile_id = _uuid_or_none(package.get("assigned_agent_profile_id"))
            if profile_id is None:
                _reject("plan_agent_unavailable")
            profile = profiles.get(profile_id)
            member = members.get(profile_id)
            self._validate_assignment(
                package=package,
                profile=profile,
                member=member,
                team=team,
                snapshot=snapshot,
                snapshot_team=snapshot_team,
            )
            assert profile is not None
            catalog = catalogs.get(profile_id)
            if catalog is None:
                catalog = self._catalog(task, profile)
                catalogs[profile_id] = catalog
            self._validate_capabilities(package, catalog)

            requirements = _resource_requirements(package)
            runtime_usage = self._runtime_usage(
                package=package,
                profile=profile,
                runtime_space=runtime_space,
            )
            workspace_usage = self._workspace_usage(
                package=package,
                profile=profile,
                runtime_space=runtime_space,
            )
            self._validate_runtime_capacity(
                workspace_id=task.workspace_id,
                runtime_space_id=runtime_space_id,
                runtime_space=runtime_space,
                usage=runtime_usage,
                scheduler_limits=scheduler_policy.resource_limits,
            )
            self._validate_workspace_capacity(task.workspace_id, workspace_usage)
            if requirements and runtime_space is None and runtime_space_id is None:
                _reject("plan_runtime_space_required")

            total_estimated_cost += _estimated_cost(package)
            provider_model = self._provider_key(task.workspace_id, profile)
            provider_keys.add((profile.id, provider_model[0], provider_model[1]))

        try:
            CostAccountingService(self._session).assert_projected_budget_available(
                task.workspace_id,
                estimated_cost=total_estimated_cost,
                currency="USD",
            )
        except CostBudgetExceededError:
            _reject("plan_cost_budget_exceeded")

        for _profile_id, provider_name, model in sorted(
            provider_keys,
            key=lambda item: (str(item[0]), item[1], item[2]),
        ):
            try:
                CostAccountingService(self._session).assert_budget_available(
                    task.workspace_id,
                    provider=provider_name,
                    model=model,
                )
            except CostBudgetExceededError:
                _reject("plan_cost_budget_exceeded")
            except ValueError:
                _reject("plan_model_provider_unavailable")

    def _active_team(self, task: Task) -> AgentTeam | None:
        if task.agent_team_id is None:
            return None
        return self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == task.workspace_id,
                AgentTeam.id == task.agent_team_id,
                AgentTeam.status == _ACTIVE_STATUS,
            )
        )

    def _validate_team_snapshot(
        self,
        team: AgentTeam,
        snapshot: dict[str, object] | None,
    ) -> dict[str, object]:
        if not isinstance(snapshot, dict):
            _reject("plan_authorization_stale")
        raw_team = snapshot.get("team")
        if not isinstance(raw_team, dict):
            _reject("plan_authorization_stale")
        if str(raw_team.get("id") or "") != str(team.id):
            _reject("plan_authorization_stale")
        version = raw_team.get("capability_policy_version")
        if isinstance(version, bool) or not isinstance(version, int):
            _reject("plan_authorization_stale")
        if version != team.capability_policy_version:
            _reject("plan_authorization_stale")
        return raw_team

    def _profiles(self, workspace_id: UUID, profile_ids: set[UUID]) -> dict[UUID, AgentProfile]:
        if not profile_ids:
            return {}
        return {
            profile.id: profile
            for profile in self._session.scalars(
                select(AgentProfile).where(
                    AgentProfile.workspace_id == workspace_id,
                    AgentProfile.id.in_(profile_ids),
                )
            ).all()
        }

    def _members(
        self,
        workspace_id: UUID,
        team_id: UUID,
        profile_ids: set[UUID],
    ) -> dict[UUID, AgentTeamMember]:
        if not profile_ids:
            return {}
        return {
            member.agent_profile_id: member
            for member in self._session.scalars(
                select(AgentTeamMember).where(
                    AgentTeamMember.workspace_id == workspace_id,
                    AgentTeamMember.agent_team_id == team_id,
                    AgentTeamMember.agent_profile_id.in_(profile_ids),
                )
            ).all()
        }

    def _validate_assignment(
        self,
        *,
        package: dict[str, object],
        profile: AgentProfile | None,
        member: AgentTeamMember | None,
        team: AgentTeam,
        snapshot: dict[str, object] | None,
        snapshot_team: dict[str, object],
    ) -> None:
        if profile is None or profile.status != _ACTIVE_STATUS:
            _reject("plan_agent_unavailable")
        assert profile is not None
        is_manager = team.manager_agent_profile_id == profile.id
        if member is None and not is_manager:
            _reject("plan_agent_unavailable")
        if member is not None:
            if member.status != _ACTIVE_STATUS or not member.accepts_tasks:
                _reject("plan_agent_unavailable")
            actual_role = member.team_role
        else:
            actual_role = "project_manager"

        required_role = _required_text(package, "required_role")
        if not _labels_match(required_role, actual_role):
            _reject("plan_agent_role_mismatch")
        if not _skills_available(
            profile,
            member,
            _required_strings(package, "required_skills", "plan_agent_skill_unavailable"),
        ):
            _reject("plan_agent_skill_unavailable")

        snapshot_version = _snapshot_agent_version(snapshot, profile.id)
        if snapshot_version is None or snapshot_version != profile.version:
            _reject("plan_agent_snapshot_stale")
        if snapshot_team.get("status") != _ACTIVE_STATUS:
            _reject("plan_authorization_stale")

    def _catalog(self, task: Task, profile: AgentProfile) -> EffectiveCapabilityCatalogResponse:
        try:
            return EffectiveCapabilityCatalogService(self._session).build(
                workspace_id=task.workspace_id,
                agent_profile_id=profile.id,
                team_id=task.agent_team_id,
            )
        except (DomainError, ValueError):
            _reject("plan_capability_policy_invalid")
        raise AssertionError("unreachable")

    def _validate_capabilities(
        self,
        package: dict[str, object],
        catalog: EffectiveCapabilityCatalogResponse,
    ) -> None:
        required_tools = _required_strings(
            package,
            "required_tools",
            "plan_capability_policy_invalid",
        )
        required_resource_ids = _required_resource_ids(package)
        tools = {item.descriptor.name for item in catalog.tools}
        resources = {item.resource.id for item in catalog.resources}
        if any(tool not in tools for tool in required_tools):
            _reject("plan_tool_unavailable")
        if any(resource_id not in resources for resource_id in required_resource_ids):
            _reject("plan_resource_unavailable")

    def _scheduler_policy(
        self,
        workspace_id: UUID,
        team: AgentTeam,
    ) -> WorkspaceSchedulerPolicy:
        override = (
            team.default_task_policy.get("scheduler")
            if isinstance(team.default_task_policy, dict)
            else None
        )
        return SchedulerPolicyResolver(self._session).policy_for(
            workspace_id,
            override=override if isinstance(override, dict) else None,
        )

    def _runtime_space(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID | None,
    ) -> RuntimeSpace | None:
        if runtime_space_id is None:
            return None
        runtime_space = self._session.scalar(
            select(RuntimeSpace).where(
                RuntimeSpace.workspace_id == workspace_id,
                RuntimeSpace.id == runtime_space_id,
            )
        )
        if runtime_space is None or runtime_space.status != _ACTIVE_STATUS:
            _reject("plan_runtime_space_unavailable")
        return runtime_space

    def _runtime_usage(
        self,
        *,
        package: dict[str, object],
        profile: AgentProfile,
        runtime_space: RuntimeSpace | None,
    ) -> dict[str, int]:
        usage: dict[str, int] = {_ACTIVE_RUNS_KEY: 1}
        if runtime_space is not None:
            merge_usage_max(
                usage, positive_int_usage(runtime_space.policy.get("resource_requirements"))
            )
            merge_usage_max(
                usage, positive_int_usage(runtime_space.policy.get("reservation_usage"))
            )
        merge_usage_max(
            usage, positive_int_usage(profile.runtime_policy.get("resource_requirements"))
        )
        merge_usage_max(usage, positive_int_usage(profile.runtime_policy.get("reservation_usage")))
        merge_usage_max(usage, _resource_requirements(package))
        return usage

    def _workspace_usage(
        self,
        *,
        package: dict[str, object],
        profile: AgentProfile,
        runtime_space: RuntimeSpace | None,
    ) -> dict[str, int]:
        usage: dict[str, int] = {_ACTIVE_RUNS_KEY: 1}
        if runtime_space is not None:
            merge_usage_max(
                usage,
                positive_int_usage(runtime_space.policy.get("workspace_reservation_usage")),
            )
        merge_usage_max(
            usage,
            positive_int_usage(profile.runtime_policy.get("workspace_reservation_usage")),
        )
        merge_workspace_reservation_usage(usage, profile.runtime_policy.get("reservation_usage"))
        merge_usage_max(
            usage, positive_int_usage(profile.runtime_policy.get("resource_requirements"))
        )
        merge_usage_max(usage, _resource_requirements(package))
        return usage

    def _validate_runtime_capacity(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID | None,
        runtime_space: RuntimeSpace | None,
        usage: dict[str, int],
        scheduler_limits: dict[str, float] | None,
    ) -> None:
        for key, amount in usage.items():
            limit = scheduler_limits.get(key) if scheduler_limits is not None else None
            if limit is not None and amount > limit:
                _reject("plan_resource_limit_exceeded")
        requires_runtime = any(key != _ACTIVE_RUNS_KEY for key in usage)
        if runtime_space is None:
            if requires_runtime or runtime_space_id is not None:
                _reject(
                    "plan_runtime_space_required"
                    if runtime_space_id is None
                    else "plan_runtime_space_unavailable"
                )
            return
        quotas = {
            quota.quota_key: quota
            for quota in self._session.scalars(
                select(RuntimeSpaceQuota).where(
                    RuntimeSpaceQuota.workspace_id == workspace_id,
                    RuntimeSpaceQuota.runtime_space_id == runtime_space.id,
                    RuntimeSpaceQuota.status == _ACTIVE_STATUS,
                    RuntimeSpaceQuota.quota_key.in_(usage),
                )
            ).all()
        }
        for key, amount in usage.items():
            quota = quotas.get(key)
            if quota is not None and quota.reserved_value + amount > quota.limit_value:
                _reject("plan_runtime_quota_exceeded")

    def _validate_workspace_capacity(self, workspace_id: UUID, usage: dict[str, int]) -> None:
        quotas = {
            quota.quota_key: quota
            for quota in self._session.scalars(
                select(WorkspaceQuota).where(
                    WorkspaceQuota.workspace_id == workspace_id,
                    WorkspaceQuota.status == _ACTIVE_STATUS,
                    WorkspaceQuota.quota_key.in_(usage),
                )
            ).all()
        }
        for key, amount in usage.items():
            quota = quotas.get(key)
            if quota is not None and quota.reserved_value + amount > quota.limit_value:
                _reject("plan_workspace_quota_exceeded")

    def _provider_key(self, workspace_id: UUID, profile: AgentProfile) -> tuple[str, str]:
        try:
            resolution = ModelProviderResolutionService(self._session).resolve_snapshot_for_agent(
                workspace_id=workspace_id,
                agent_credential_id=profile.model_provider_credential_id,
                agent_model=profile.model,
            )
        except ValueError:
            _reject("plan_model_provider_unavailable")
        provider = resolution.provider or "openai"
        model = resolution.selected_model or profile.model
        if not provider.strip() or not model.strip():
            _reject("plan_model_provider_unavailable")
        return provider, model


def _packages(plan: dict[str, object]) -> list[dict[str, object]]:
    raw_packages = plan.get("work_packages")
    if not isinstance(raw_packages, list) or not raw_packages:
        _reject("plan_invalid")
    packages = [package for package in raw_packages if isinstance(package, dict)]
    if len(packages) != len(raw_packages):
        _reject("plan_invalid")
    return packages


def _assigned_profile_ids(packages: list[dict[str, object]]) -> set[UUID]:
    ids: set[UUID] = set()
    for package in packages:
        profile_id = _uuid_or_none(package.get("assigned_agent_profile_id"))
        if profile_id is not None:
            ids.add(profile_id)
    return ids


def _snapshot_agent_version(snapshot: dict[str, object] | None, profile_id: UUID) -> int | None:
    if not isinstance(snapshot, dict):
        return None
    raw_agents = snapshot.get("agents")
    if isinstance(raw_agents, list):
        for raw_agent in raw_agents:
            if not isinstance(raw_agent, dict) or str(raw_agent.get("id") or "") != str(profile_id):
                continue
            version = raw_agent.get("version")
            return version if isinstance(version, int) and not isinstance(version, bool) else None
    raw_members = snapshot.get("members")
    if isinstance(raw_members, list):
        for raw_member in raw_members:
            if not isinstance(raw_member, dict) or str(
                raw_member.get("agent_profile_id") or ""
            ) != str(profile_id):
                continue
            raw_agent = raw_member.get("agent")
            if not isinstance(raw_agent, dict):
                return None
            version = raw_agent.get("version")
            return version if isinstance(version, int) and not isinstance(version, bool) else None
    return None


def _required_text(package: dict[str, object], key: str) -> str:
    value = package.get(key)
    if not isinstance(value, str) or not value.strip():
        _reject("plan_invalid")
    return value.strip()


def _required_strings(package: dict[str, object], key: str, code: str) -> list[str]:
    value = package.get(key, [])
    if not isinstance(value, list):
        _reject(code)
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            _reject(code)
        result.append(item.strip())
    if len(set(result)) != len(result):
        _reject(code)
    return result


def _required_resource_ids(package: dict[str, object]) -> list[UUID]:
    value = package.get("required_resource_ids", [])
    if not isinstance(value, list):
        _reject("plan_capability_policy_invalid")
    result: list[UUID] = []
    for item in value:
        resource_id = _uuid_or_none(item)
        if resource_id is None:
            _reject("plan_capability_policy_invalid")
        result.append(resource_id)
    if len(set(result)) != len(result):
        _reject("plan_capability_policy_invalid")
    return result


def _resource_requirements(package: dict[str, object]) -> dict[str, int]:
    value = package.get("resource_requirements", {})
    if not isinstance(value, dict):
        _reject("plan_resource_requirement_invalid")
    result: dict[str, int] = {}
    for key, raw_amount in value.items():
        if (
            not isinstance(key, str)
            or not key.strip()
            or isinstance(raw_amount, bool)
            or not isinstance(raw_amount, int)
            or raw_amount <= 0
        ):
            _reject("plan_resource_requirement_invalid")
        result[key.strip()] = raw_amount
    if len(result) != len(value):
        _reject("plan_resource_requirement_invalid")
    return result


def _estimated_cost(package: dict[str, object]) -> Decimal:
    raw_value = package.get("estimated_cost_usd", 0)
    try:
        value = Decimal(str(raw_value))
    except (InvalidOperation, TypeError, ValueError):
        _reject("plan_cost_invalid")
    if not value.is_finite() or value < 0 or value > _MAX_ESTIMATED_COST_USD:
        _reject("plan_cost_invalid")
    if isinstance(raw_value, float) and not isfinite(raw_value):
        _reject("plan_cost_invalid")
    return value


def _labels_match(required: str, actual: str) -> bool:
    required_label = normalize_role(required)
    actual_label = normalize_role(actual)
    return bool(
        required_label
        and actual_label
        and (
            required_label == actual_label
            or required_label in actual_label
            or actual_label in required_label
        )
    )


def _skills_available(
    profile: AgentProfile,
    member: AgentTeamMember | None,
    required_skills: list[str],
) -> bool:
    available: set[str] = set()
    if member is not None:
        available.update(
            normalize_role(str(key))
            for key, weight in member.skill_weights.items()
            if _positive_number(weight)
        )
        available.update(
            normalize_role(item) for item in member.responsibilities if isinstance(item, str)
        )
    for payload in (profile.skills, profile.capabilities):
        if not isinstance(payload, dict):
            continue
        for key in ("skills", "skill_keys", "available_skills", "names"):
            raw_values = payload.get(key)
            if isinstance(raw_values, list):
                available.update(
                    normalize_role(item)
                    for item in raw_values
                    if isinstance(item, str) and item.strip()
                )
        available.update(
            normalize_role(str(key)) for key, value in payload.items() if _positive_number(value)
        )
    return all(normalize_role(skill) in available for skill in required_skills)


def _positive_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float | Decimal):
        return value > 0
    return False


def _uuid_or_none(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def merge_workspace_reservation_usage(target: dict[str, int], value: object) -> None:
    if not isinstance(value, dict):
        return
    for key in ("docker_runtimes", "self_hosted_jobs"):
        amount = value.get(key)
        if isinstance(amount, int) and not isinstance(amount, bool) and amount > 0:
            target[key] = max(target.get(key, 0), amount)


def _reject(code: str) -> NoReturn:
    raise ProjectPlanValidationError("Project plan is not feasible", code=code)
