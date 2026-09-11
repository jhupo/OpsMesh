"""Workspace-owned orchestration definitions and task-plan admission."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.observability.audit_service import AuditService
from backend.app.capabilities.models import CapabilityResource, McpServer, McpToolAllowlist
from backend.app.core.pagination import PageParams
from backend.app.db.errors import commit_or_raise_conflict, flush_or_raise_conflict
from backend.app.db.pagination import page_scalars
from backend.app.orchestration.workflows.definition_commands import (
    OrchestrationDefinitionCreate,
    OrchestrationDefinitionUpdate,
)
from backend.app.orchestration.models import OrchestrationDefinition, OrchestrationRevision
from backend.app.orchestration.policies.statuses import ACTIVE_RUN_STATUS_VALUES
from backend.app.orchestration.planning.team_project_plan import ProjectPlanStepMaterializer
from backend.app.planning.member_matching import MemberMatchingService
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.planning.plan_feasibility import PlanFeasibilityService
from backend.app.planning.project_plan_validation import (
    ProjectPlanValidationError,
    validate_project_plan,
    validate_workflow_graph,
)
from backend.app.planning.workflow_contracts import WorkflowNode
from backend.app.runs.models import AgentRun
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.status import TERMINAL_TASK_STATUSES
from backend.app.teams.models import AgentTeam
from backend.app.teams.snapshots import build_team_snapshot


class OrchestrationDefinitionError(ValueError):
    def __init__(self, message: str, *, code: str = "orchestration_invalid") -> None:
        super().__init__(message)
        self.code = code


class OrchestrationDefinitionService:
    """Manage versioned, workspace-scoped user orchestration definitions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_definitions(
        self,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[OrchestrationDefinition], int]:
        statement = select(OrchestrationDefinition).where(
            OrchestrationDefinition.workspace_id == workspace_id
        )
        if status is not None:
            statement = statement.where(OrchestrationDefinition.status == status)
        statement = statement.order_by(
            OrchestrationDefinition.updated_at.desc(),
            OrchestrationDefinition.id.desc(),
        )
        return page_scalars(self._session, statement, page)

    def get_definition(
        self,
        workspace_id: UUID,
        orchestration_definition_id: UUID,
    ) -> OrchestrationDefinition | None:
        return self._session.scalar(
            select(OrchestrationDefinition).where(
                OrchestrationDefinition.workspace_id == workspace_id,
                OrchestrationDefinition.id == orchestration_definition_id,
            )
        )

    def create_definition(
        self,
        workspace_id: UUID,
        request: OrchestrationDefinitionCreate,
        actor_user_id: UUID | None = None,
        *,
        commit: bool = True,
    ) -> OrchestrationDefinition:
        nodes = self._validate_nodes(workspace_id, request.nodes)
        definition = OrchestrationDefinition(
            workspace_id=workspace_id,
            created_by_user_id=actor_user_id,
            key=request.key,
            name=request.name,
            description=request.description,
            definition={"definition_version": 1, "nodes": nodes},
            version=1,
            status="draft",
        )
        self._session.add(definition)
        flush_or_raise_conflict(self._session, "Orchestration key already exists")
        self._record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="orchestration.created",
            target_id=definition.id,
            metadata={"key": definition.key, "version": definition.version},
        )
        if commit:
            commit_or_raise_conflict(self._session, "Orchestration key already exists")
            self._session.refresh(definition)
        return definition

    def list_revisions(
        self,
        workspace_id: UUID,
        definition_id: UUID,
        page: PageParams,
    ) -> tuple[list[OrchestrationRevision], int]:
        if self.get_definition(workspace_id, definition_id) is None:
            raise OrchestrationDefinitionError(
                "Orchestration definition not found", code="orchestration_not_found"
            )
        return page_scalars(
            self._session,
            select(OrchestrationRevision)
            .where(
                OrchestrationRevision.workspace_id == workspace_id,
                OrchestrationRevision.definition_id == definition_id,
            )
            .order_by(OrchestrationRevision.version.desc()),
            page,
        )

    def get_revision(
        self,
        workspace_id: UUID,
        definition_id: UUID,
        version: int,
    ) -> OrchestrationRevision | None:
        return self._session.scalar(
            select(OrchestrationRevision).where(
                OrchestrationRevision.workspace_id == workspace_id,
                OrchestrationRevision.definition_id == definition_id,
                OrchestrationRevision.version == version,
            )
        )

    def update_definition(
        self,
        workspace_id: UUID,
        orchestration_definition_id: UUID,
        request: OrchestrationDefinitionUpdate,
        actor_user_id: UUID | None = None,
        *,
        commit: bool = True,
    ) -> OrchestrationDefinition:
        definition = self._require_definition(workspace_id, orchestration_definition_id)
        changes = request.model_dump(exclude_unset=True)
        if request.expected_version != definition.version:
            raise OrchestrationDefinitionError(
                "Definition changed; reload before editing", code="orchestration_version_mismatch"
            )
        next_nodes = (
            request.nodes if request.nodes is not None else self._nodes_from_definition(definition)
        )
        serialized_nodes = self._validate_nodes(workspace_id, next_nodes)
        before_version = definition.version
        if request.name is not None:
            definition.name = request.name
        if request.description is not None:
            definition.description = request.description
        definition.definition = {
            "definition_version": 1,
            "nodes": serialized_nodes,
        }
        definition.version = before_version + 1
        definition.status = "draft"
        definition.published_at = None
        self._session.flush([definition])
        self._record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="orchestration.updated",
            target_id=definition.id,
            metadata={
                "key": definition.key,
                "version": definition.version,
                "previous_version": before_version,
                "changed_fields": sorted(changes),
            },
        )
        if commit:
            self._session.commit()
            self._session.refresh(definition)
        return definition

    def validate_definition(
        self,
        workspace_id: UUID,
        orchestration_definition_id: UUID,
    ) -> tuple[OrchestrationDefinition, list[str]]:
        definition = self._require_definition(workspace_id, orchestration_definition_id)
        try:
            self._validate_nodes(workspace_id, self._nodes_from_definition(definition))
        except OrchestrationDefinitionError as exc:
            return definition, [f"{exc.code}: {exc}"]
        return definition, []

    def publish_definition(
        self,
        workspace_id: UUID,
        orchestration_definition_id: UUID,
        actor_user_id: UUID | None = None,
        *,
        commit: bool = True,
    ) -> OrchestrationDefinition:
        definition = self._require_definition(workspace_id, orchestration_definition_id)
        self._validate_nodes(workspace_id, self._nodes_from_definition(definition))
        if definition.status == "archived":
            raise OrchestrationDefinitionError(
                "Edit the archived definition before publishing a new revision",
                code="orchestration_archived",
            )
        if definition.status == "published":
            return definition
        self._session.add(
            OrchestrationRevision(
                workspace_id=workspace_id,
                definition_id=definition.id,
                version=definition.version,
                name=definition.name,
                definition=deepcopy(definition.definition),
            )
        )
        definition.status = "published"
        definition.published_at = datetime.now(UTC)
        self._session.flush([definition])
        self._record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="orchestration.published",
            target_id=definition.id,
            metadata={"key": definition.key, "version": definition.version},
        )
        if commit:
            self._session.commit()
            self._session.refresh(definition)
        return definition

    def archive_definition(
        self,
        workspace_id: UUID,
        orchestration_definition_id: UUID,
        actor_user_id: UUID | None = None,
        *,
        commit: bool = True,
    ) -> OrchestrationDefinition:
        definition = self._require_definition(workspace_id, orchestration_definition_id)
        definition.status = "archived"
        definition.published_at = None
        self._session.flush([definition])
        self._record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="orchestration.archived",
            target_id=definition.id,
            metadata={"key": definition.key, "version": definition.version},
        )
        if commit:
            self._session.commit()
            self._session.refresh(definition)
        return definition

    def apply_to_task(
        self,
        task: Task,
        orchestration_definition_id: UUID,
        orchestration_version: int | None = None,
        actor_user_id: UUID | None = None,
    ) -> Task:
        self._session.flush([task])
        locked_task = self._session.scalar(
            select(Task)
            .where(Task.workspace_id == task.workspace_id, Task.id == task.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked_task is None:
            raise OrchestrationDefinitionError(
                "Task not found", code="orchestration_task_not_found"
            )
        task = locked_task
        if task.agent_team_id is None:
            raise OrchestrationDefinitionError(
                "A team is required to apply an orchestration",
                code="orchestration_team_required",
            )
        if task.status in TERMINAL_TASK_STATUSES:
            raise OrchestrationDefinitionError(
                "Terminal tasks cannot receive a new orchestration",
                code="orchestration_task_terminal",
            )
        if (
            self._session.scalar(
                select(TaskStep.id)
                .where(TaskStep.workspace_id == task.workspace_id, TaskStep.task_id == task.id)
                .limit(1)
            )
            is not None
        ):
            raise OrchestrationDefinitionError(
                "Task already has materialized work; use explicit plan mutation",
                code="orchestration_task_has_steps",
            )
        if (
            self._session.scalar(
                select(AgentRun.id)
                .where(
                    AgentRun.workspace_id == task.workspace_id,
                    AgentRun.task_id == task.id,
                    AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
                )
                .limit(1)
            )
            is not None
        ):
            raise OrchestrationDefinitionError(
                "Task has an active run",
                code="orchestration_task_active_run",
            )

        definition = self._require_definition(task.workspace_id, orchestration_definition_id)
        if definition.status == "archived":
            raise OrchestrationDefinitionError(
                "Archived orchestrations cannot be applied",
                code="orchestration_not_published",
            )
        revision_query = select(OrchestrationRevision).where(
            OrchestrationRevision.workspace_id == task.workspace_id,
            OrchestrationRevision.definition_id == definition.id,
        )
        if orchestration_version is not None:
            revision_query = revision_query.where(
                OrchestrationRevision.version == orchestration_version
            )
        revision = self._session.scalar(
            revision_query.order_by(OrchestrationRevision.version.desc()).limit(1)
        )
        if revision is None:
            raise OrchestrationDefinitionError(
                "Published orchestration version not found",
                code="orchestration_not_published",
            )
        if not isinstance(task.team_snapshot, dict):
            task.team_snapshot = build_team_snapshot(
                self._session,
                workspace_id=task.workspace_id,
                team_id=task.agent_team_id,
            )
        plan = self._build_plan(task, definition, revision)
        try:
            validate_project_plan(plan, task.team_snapshot)
            PlanFeasibilityService(self._session).validate(task=task, plan=plan)
        except ProjectPlanValidationError as exc:
            raise OrchestrationDefinitionError(str(exc), code=exc.code) from exc

        ProjectPlanStepMaterializer(self._session).materialize(task, plan)
        task.project_plan = plan
        task.orchestration_definition_id = definition.id
        task.orchestration_version = revision.version
        attempt = TaskPlanningAttempt(
            workspace_id=task.workspace_id,
            task_id=task.id,
            planner_agent_profile_id=task.owner_agent_profile_id,
            attempt_number=self._next_attempt_number(task),
            status="completed",
            strategy="user_authored",
            input_snapshot={
                "orchestration_definition_id": str(definition.id),
                "orchestration_version": revision.version,
            },
            output_snapshot=plan,
            validation_errors=[],
            retry_count=0,
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        self._session.add(attempt)
        self._session.flush([task, attempt])
        raw_packages = plan.get("work_packages")
        work_package_count = len(raw_packages) if isinstance(raw_packages, list) else 0
        TaskMessageAppendService(self._session).append_for_task(
            task,
            message_type="planning.completed",
            body="User-authored orchestration admitted.",
            payload={
                "attempt_id": str(attempt.id),
                "strategy": "user_authored",
                "orchestration_definition_id": str(definition.id),
                "orchestration_version": revision.version,
                "work_package_count": work_package_count,
            },
        )
        self._record_audit(
            workspace_id=task.workspace_id,
            actor_user_id=actor_user_id,
            action="task.orchestration_applied",
            target_id=task.id,
            metadata={
                "orchestration_definition_id": str(definition.id),
                "orchestration_version": revision.version,
                "work_package_count": work_package_count,
            },
        )
        self._session.flush()
        return task

    def _build_plan(
        self,
        task: Task,
        definition: OrchestrationDefinition,
        revision: OrchestrationRevision,
    ) -> dict[str, object]:
        raw_nodes = self._nodes_from_definition(revision)
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == task.workspace_id,
                AgentTeam.id == task.agent_team_id,
                AgentTeam.status == "active",
            )
        )
        if team is None:
            raise OrchestrationDefinitionError(
                "Active team not found",
                code="orchestration_team_unavailable",
            )
        matcher = MemberMatchingService(self._session)
        packages: list[dict[str, object]] = []
        for node in raw_nodes:
            if node.node_type == "subworkflow":
                if node.subworkflow_definition_id == definition.id:
                    raise OrchestrationDefinitionError(
                        "A subworkflow cannot reference its own definition",
                        code="orchestration_recursive_subworkflow",
                    )
                self._validate_subworkflow_reference(task.workspace_id, node)
            assigned_id = node.assigned_agent_profile_id
            if node.node_type in {"condition", "join", "start", "end"}:
                assigned_id = None
            elif assigned_id is None:
                assigned_id = self._match_agent(
                    task,
                    team,
                    matcher,
                    node,
                )
            if assigned_id is None and node.node_type not in {"condition", "join", "start", "end"}:
                raise OrchestrationDefinitionError(
                    f"No team agent matches orchestration node {node.package_id}",
                    code="orchestration_agent_unavailable",
                )
            package = node.model_dump(mode="json", by_alias=True, exclude_none=True)
            package["assigned_agent_profile_id"] = str(assigned_id)
            packages.append(package)
        planner_id = task.owner_agent_profile_id or team.manager_agent_profile_id
        return {
            "plan_version": 1,
            "plan_id": f"orchestration:{definition.id}:{revision.version}:{uuid4()}",
            "objective": task.title,
            "planner_agent_profile_id": str(planner_id) if planner_id is not None else None,
            "generated_at": datetime.now(UTC).isoformat(),
            "strategy": "user_authored",
            "orchestration_definition_id": str(definition.id),
            "orchestration_version": revision.version,
            "work_packages": packages,
        }

    def _match_agent(
        self,
        task: Task,
        team: AgentTeam,
        matcher: MemberMatchingService,
        node: WorkflowNode,
    ) -> UUID | None:
        if (
            node.required_role.strip().lower().replace("-", "_")
            in {
                "project_manager",
                "product_manager",
                "program_manager",
                "manager",
                "pm",
            }
            and team.manager_agent_profile_id is not None
        ):
            return team.manager_agent_profile_id
        assert isinstance(task.team_snapshot, dict)
        match = matcher.match(
            team_snapshot=task.team_snapshot,
            required_role=node.required_role,
            required_skills=list(node.required_skills),
            workspace_id=task.workspace_id,
        )
        return match.agent_profile_id if match is not None else None

    def _validate_nodes(
        self,
        workspace_id: UUID,
        nodes: list[WorkflowNode],
    ) -> list[dict[str, object]]:
        if not nodes or len(nodes) > 128:
            raise OrchestrationDefinitionError(
                "Orchestration must contain 1 to 128 nodes",
                code="orchestration_node_count_invalid",
            )
        node_ids = [node.package_id for node in nodes]
        if len(set(node_ids)) != len(node_ids):
            raise OrchestrationDefinitionError(
                "Orchestration node IDs must be unique",
                code="orchestration_duplicate_node",
            )
        reserved = {"manager-planning", "manager-summary"}
        if reserved.intersection(node_ids):
            raise OrchestrationDefinitionError(
                "Orchestration node ID is reserved",
                code="orchestration_reserved_node",
            )
        try:
            validate_workflow_graph(
                [node.model_dump(mode="json", by_alias=True, exclude_none=True) for node in nodes],
                set(node_ids),
            )
        except ProjectPlanValidationError as exc:
            raise OrchestrationDefinitionError(str(exc), code=exc.code) from exc

        profile_ids = {
            node.assigned_agent_profile_id
            for node in nodes
            if node.assigned_agent_profile_id is not None
        }
        if profile_ids:
            profiles = self._session.scalars(
                select(AgentProfile).where(
                    AgentProfile.workspace_id == workspace_id,
                    AgentProfile.id.in_(profile_ids),
                    AgentProfile.status == "active",
                )
            ).all()
            if {profile.id for profile in profiles} != profile_ids:
                raise OrchestrationDefinitionError(
                    "Orchestration references an unavailable agent profile",
                    code="orchestration_agent_reference_invalid",
                )

        resource_ids = {resource_id for node in nodes for resource_id in node.required_resource_ids}
        if resource_ids:
            resources = self._session.scalars(
                select(CapabilityResource).where(
                    CapabilityResource.workspace_id == workspace_id,
                    CapabilityResource.id.in_(resource_ids),
                    CapabilityResource.status == "active",
                )
            ).all()
            if {resource.id for resource in resources} != resource_ids:
                raise OrchestrationDefinitionError(
                    "Orchestration references an unavailable resource",
                    code="orchestration_resource_reference_invalid",
                )

        server_ids = {item.mcp_server_id for node in nodes for item in node.required_mcp_tools}
        servers: dict[UUID, McpServer] = {}
        if server_ids:
            servers = {
                server.id: server
                for server in self._session.scalars(
                    select(McpServer).where(
                        McpServer.workspace_id == workspace_id,
                        McpServer.id.in_(server_ids),
                        McpServer.status == "active",
                    )
                ).all()
            }
        if set(servers) != server_ids:
            raise OrchestrationDefinitionError(
                "Orchestration references an unavailable MCP server",
                code="orchestration_mcp_server_reference_invalid",
            )
        for node in nodes:
            for item in node.required_mcp_tools:
                allowlist = self._session.scalar(
                    select(McpToolAllowlist).where(
                        McpToolAllowlist.workspace_id == workspace_id,
                        McpToolAllowlist.mcp_server_id == item.mcp_server_id,
                        McpToolAllowlist.tool_name == item.tool_name,
                        McpToolAllowlist.status == "active",
                    )
                )
                if allowlist is None or (
                    item.mcp_tool_allowlist_id is not None
                    and allowlist.id != item.mcp_tool_allowlist_id
                ):
                    raise OrchestrationDefinitionError(
                        f"MCP tool {item.tool_name} is not allowlisted",
                        code="orchestration_mcp_tool_reference_invalid",
                    )

        for node in nodes:
            if node.node_type == "subworkflow":
                self._validate_subworkflow_reference(workspace_id, node)

        return [node.model_dump(mode="json", by_alias=True, exclude_none=True) for node in nodes]

    def _validate_subworkflow_reference(
        self,
        workspace_id: UUID,
        node: WorkflowNode,
    ) -> None:
        definition_id = node.subworkflow_definition_id
        if definition_id is None:
            raise OrchestrationDefinitionError(
                "Subworkflow node requires a definition",
                code="orchestration_subworkflow_definition_invalid",
            )
        definition = self._session.scalar(
            select(OrchestrationDefinition).where(
                OrchestrationDefinition.workspace_id == workspace_id,
                OrchestrationDefinition.id == definition_id,
                OrchestrationDefinition.status != "archived",
            )
        )
        if definition is None:
            raise OrchestrationDefinitionError(
                "Subworkflow definition is unavailable",
                code="orchestration_subworkflow_definition_invalid",
            )
        revision_query = select(OrchestrationRevision.id).where(
            OrchestrationRevision.workspace_id == workspace_id,
            OrchestrationRevision.definition_id == definition.id,
        )
        if node.subworkflow_version is not None:
            revision_query = revision_query.where(
                OrchestrationRevision.version == node.subworkflow_version
            )
        if self._session.scalar(revision_query.limit(1)) is None:
            raise OrchestrationDefinitionError(
                "Subworkflow definition has no published revision",
                code="orchestration_subworkflow_revision_invalid",
            )

    def _nodes_from_definition(
        self,
        definition: OrchestrationDefinition | OrchestrationRevision,
    ) -> list[WorkflowNode]:
        raw_definition = definition.definition
        raw_nodes = raw_definition.get("nodes") if isinstance(raw_definition, dict) else None
        if not isinstance(raw_nodes, list):
            raise OrchestrationDefinitionError(
                "Stored orchestration has no nodes",
                code="orchestration_definition_invalid",
            )
        try:
            return [WorkflowNode.model_validate(item) for item in raw_nodes]
        except ValidationError as exc:
            raise OrchestrationDefinitionError(
                "Stored orchestration node is invalid",
                code="orchestration_definition_invalid",
            ) from exc

    def _require_definition(
        self,
        workspace_id: UUID,
        orchestration_definition_id: UUID,
    ) -> OrchestrationDefinition:
        definition = self._session.scalar(
            select(OrchestrationDefinition)
            .where(
                OrchestrationDefinition.workspace_id == workspace_id,
                OrchestrationDefinition.id == orchestration_definition_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if definition is None:
            raise OrchestrationDefinitionError(
                "Orchestration definition not found",
                code="orchestration_not_found",
            )
        return definition

    def _next_attempt_number(self, task: Task) -> int:
        current = self._session.scalar(
            select(func.coalesce(func.max(TaskPlanningAttempt.attempt_number), 0)).where(
                TaskPlanningAttempt.workspace_id == task.workspace_id,
                TaskPlanningAttempt.task_id == task.id,
            )
        )
        return int(current or 0) + 1

    def _record_audit(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        action: str,
        target_id: UUID,
        metadata: dict[str, object],
        target_type: str | None = None,
    ) -> None:
        service = AuditService(self._session)
        resolved_target_type = target_type or (
            "task" if action.startswith("task.") else "orchestration_definition"
        )
        if actor_user_id is None:
            service.record_system_action(
                workspace_id=workspace_id,
                action=action,
                target_type=resolved_target_type,
                target_id=target_id,
                metadata=metadata,
            )
        else:
            service.record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action=action,
                target_type=resolved_target_type,
                target_id=target_id,
                metadata=metadata,
            )


__all__ = ["OrchestrationDefinitionError", "OrchestrationDefinitionService"]
