"""Application of a validated workflow definition to a durable task plan."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.domains.orchestration.models import OrchestrationDefinition, OrchestrationRevision
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.tasks.message_append import TaskMessageAppendService
from backend.app.domains.orchestration.tasks.models import Task, TaskStep
from backend.app.domains.orchestration.tasks.state import TERMINAL_TASK_STATUSES
from backend.app.domains.orchestration.workflows.definitions.contracts import WorkflowNode
from backend.app.domains.orchestration.workflows.definitions.graph import (
    ProjectPlanValidationError,
    WorkflowGraphError,
    validate_project_plan,
)
from backend.app.domains.orchestration.workflows.definitions.service import (
    OrchestrationDefinitionError,
)
from backend.app.domains.orchestration.workflows.definitions.validation import (
    DefinitionValidationError,
    DefinitionValidationService,
    nodes_from_definition,
)
from backend.app.domains.orchestration.workflows.planning.attempt_models import TaskPlanningAttempt
from backend.app.domains.orchestration.workflows.planning.feasibility import PlanFeasibilityService
from backend.app.domains.orchestration.workflows.planning.member_matching import (
    MemberMatchingService,
)
from backend.app.domains.orchestration.workflows.planning.team_project_plan import (
    ProjectPlanStepMaterializer,
)
from backend.app.domains.orchestration.workflows.statuses import ACTIVE_RUN_STATUS_VALUES
from backend.app.domains.workspace.teams.models import AgentTeam
from backend.app.domains.workspace.teams.runtime.snapshots import build_team_snapshot
from backend.app.observability.audit.service import AuditService


class OrchestrationDefinitionApplicationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._validator = DefinitionValidationService(session)

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
        except (ProjectPlanValidationError, WorkflowGraphError) as exc:
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
        try:
            raw_nodes = nodes_from_definition(revision)
        except DefinitionValidationError as exc:
            raise OrchestrationDefinitionError(str(exc), code=exc.code) from exc
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
                try:
                    self._validator.require_subworkflow_reference(
                        task.workspace_id,
                        node,
                        parent_definition_id=definition.id,
                    )
                except DefinitionValidationError as exc:
                    raise OrchestrationDefinitionError(str(exc), code=exc.code) from exc
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

    def _next_attempt_number(self, task: Task) -> int:
        current = self._session.scalar(
            select(func.coalesce(func.max(TaskPlanningAttempt.attempt_number), 0)).where(
                TaskPlanningAttempt.workspace_id == task.workspace_id,
                TaskPlanningAttempt.task_id == task.id,
            )
        )
        return int(current or 0) + 1

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

    def _record_audit(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        action: str,
        target_id: UUID,
        metadata: dict[str, object],
    ) -> None:
        service = AuditService(self._session)
        if actor_user_id is None:
            service.record_system_action(
                workspace_id=workspace_id,
                action=action,
                target_type="task",
                target_id=target_id,
                metadata=metadata,
            )
        else:
            service.record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action=action,
                target_type="task",
                target_id=target_id,
                metadata=metadata,
            )
