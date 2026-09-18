"""Conservative dependency evidence for destructive plugin lifecycle operations."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.agents.profiles.models import AgentProfile
from backend.app.domains.capabilities.mcp.models import McpToolAllowlist
from backend.app.domains.capabilities.plugins.models import PluginBinding
from backend.app.domains.capabilities.resources.models import CapabilityResource
from backend.app.domains.capabilities.skills.models import WorkspaceSkillInstall
from backend.app.domains.integrations.automation_models import Automation, AutomationEvent
from backend.app.domains.integrations.webhooks.models import WebhookDeliveryAttempt
from backend.app.domains.orchestration.models import OrchestrationDefinition, OrchestrationRevision
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.tasks.models import Task
from backend.app.domains.workspace.teams.models import AgentTeam


def _references(value: object, needles: set[str]) -> bool:
    if isinstance(value, str):
        return value in needles
    if isinstance(value, list):
        return any(_references(item, needles) for item in value)
    if isinstance(value, dict):
        return any(str(key) in needles or _references(item, needles) for key, item in value.items())
    return False


def binding_dependents(
    session: Session, workspace_id: UUID, bindings: list[PluginBinding]
) -> list[str]:
    resource_ids = {item.resource_id for item in bindings}
    needles = {str(item) for item in resource_ids}
    server_ids = {item.resource_id for item in bindings if item.kind == "mcp_server"}
    skill_ids = {item.resource_id for item in bindings if item.kind == "skill"}
    for skill in session.scalars(
        select(WorkspaceSkillInstall).where(
            WorkspaceSkillInstall.workspace_id == workspace_id,
            WorkspaceSkillInstall.id.in_(skill_ids),
        )
    ):
        needles.add(skill.installed_key)
        needles.update(skill.installed_capability_keys)
    for tool_name, capability_key in session.execute(
        select(McpToolAllowlist.tool_name, McpToolAllowlist.capability_key).where(
            McpToolAllowlist.workspace_id == workspace_id,
            McpToolAllowlist.mcp_server_id.in_(server_ids),
        )
    ):
        needles.add(tool_name)
        if capability_key:
            needles.add(capability_key)
    found: set[str] = set()
    # Retained published revisions are dependencies even when the editable draft has changed.
    for name, query in (
        (
            "agent_teams",
            select(
                AgentTeam.id,
                AgentTeam.capability_policy,
                AgentTeam.default_task_policy,
                AgentTeam.coordination_rules,
            ).where(AgentTeam.workspace_id == workspace_id),
        ),
        (
            "workspace_skill_installs",
            select(
                WorkspaceSkillInstall.id,
                WorkspaceSkillInstall.installed_manifest,
                WorkspaceSkillInstall.config,
            ).where(
                WorkspaceSkillInstall.workspace_id == workspace_id,
                WorkspaceSkillInstall.id.not_in(resource_ids),
            ),
        ),
        (
            "agent_profiles",
            select(
                AgentProfile.id,
                AgentProfile.skills,
                AgentProfile.tool_policy,
                AgentProfile.capabilities,
            ).where(AgentProfile.workspace_id == workspace_id),
        ),
        (
            "orchestration_definitions",
            select(OrchestrationDefinition.id, OrchestrationDefinition.definition).where(
                OrchestrationDefinition.workspace_id == workspace_id
            ),
        ),
        (
            "orchestration_revisions",
            select(OrchestrationRevision.id, OrchestrationRevision.definition).where(
                OrchestrationRevision.workspace_id == workspace_id
            ),
        ),
        (
            "capability_resources",
            select(
                CapabilityResource.id,
                CapabilityResource.locator,
                CapabilityResource.default_parameters,
            ).where(CapabilityResource.workspace_id == workspace_id),
        ),
    ):
        for record in session.execute(query):
            if any(_references(value, needles) for value in record[1:]):
                found.add(f"{name}:{record[0]}")
    for row in session.scalars(
        select(Task).where(
            Task.workspace_id == workspace_id,
            Task.status.not_in(["completed", "failed", "cancelled"]),
        )
    ):
        if _references(row.project_plan, needles) or _references(row.team_snapshot, needles):
            found.add(f"tasks:{row.id}")
    for run in session.scalars(
        select(AgentRun).where(
            AgentRun.workspace_id == workspace_id,
            AgentRun.status.not_in(["completed", "failed", "cancelled"]),
        )
    ):
        if _references(run.input, needles):
            found.add(f"agent_runs:{run.id}")
    for automation in session.scalars(
        select(Automation).where(Automation.workspace_id == workspace_id)
    ):
        if (
            automation.id in resource_ids
            and automation.status == "active"
            or _references(automation.configuration, needles)
        ):
            found.add(f"automations:{automation.id}")
    for event in session.scalars(
        select(AutomationEvent).where(
            AutomationEvent.workspace_id == workspace_id,
            AutomationEvent.status.in_(["pending", "dispatched", "reply_pending", "reply_failed"]),
        )
    ):
        if event.automation_id in resource_ids or _references(event.configuration, needles):
            found.add(f"automation_events:{event.id}")
    for attempt in session.scalars(
        select(WebhookDeliveryAttempt).where(
            WebhookDeliveryAttempt.workspace_id == workspace_id,
            WebhookDeliveryAttempt.subscription_id.in_(resource_ids),
            WebhookDeliveryAttempt.status != "succeeded",
        )
    ):
        found.add(f"webhook_delivery_attempts:{attempt.id}")
    return sorted(found)
