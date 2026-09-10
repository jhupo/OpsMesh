"""Apply safe, auditable mutations to the future portion of a team plan."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.orchestration.conditions import condition_step_references
from backend.app.orchestration.statuses import ACTIVE_RUN_STATUS_VALUES
from backend.app.orchestration.team_step_project_plan import (
    ProjectPlanStepMaterializer,
    after_step_ids_for_package,
    step_dependencies_for_package,
)
from backend.app.planning.agent_plan import PlannedWork
from backend.app.planning.plan_feasibility import PlanFeasibilityService
from backend.app.planning.project_plan_validation import (
    ProjectPlanValidationError,
    validate_project_plan,
)
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.step_service import TaskStepStateService
from backend.app.tasks.step_status import TaskStepStatus
from backend.app.teams.snapshots import build_team_snapshot
from backend.app.workers.queue.redis_queue import RedisQueue

MUTABLE_STEP_STATUSES = frozenset({TaskStepStatus.QUEUED.value, TaskStepStatus.BLOCKED.value})
FINAL_STEP_STATUSES = frozenset(
    {
        TaskStepStatus.COMPLETED.value,
        TaskStepStatus.FAILED.value,
        TaskStepStatus.CANCELLED.value,
        TaskStepStatus.SKIPPED.value,
    }
)
MAX_MUTATION_OPERATIONS = 32
MAX_MUTATION_HISTORY = 128


class TaskPlanMutationError(ValueError):
    """A fail-closed plan mutation rejection with a stable machine-readable code."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TaskPlanMutationCommand:
    expected_revision: int
    operations: tuple[dict[str, object], ...]
    reason: str
    refresh_team_snapshot: bool = False
    mutation_id: str | None = None


@dataclass(slots=True)
class _MutationState:
    packages: list[dict[str, object]]
    steps: dict[str, TaskStep]
    active_run_step_ids: set[UUID]
    created_package_ids: set[str]
    changed_package_ids: set[str]
    new_work_package_ids: set[str]
    cancelled_package_ids: set[str]


class TaskPlanMutationService:
    """Mutate only queued future work while preserving execution history."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def apply(
        self,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
        command: TaskPlanMutationCommand,
        *,
        enqueue_run: bool = False,
        queue: RedisQueue | None = None,
    ) -> Task | None:
        task = self._get_task(workspace_id, task_id)
        if task is None:
            return None
        self._require_task(task)
        plan = self._copy_plan(task.project_plan)
        try:
            validate_project_plan(plan, task.team_snapshot)
        except ProjectPlanValidationError as exc:
            self._reject(exc.code, "The current project plan is invalid")

        mutation_id = self._mutation_id(command.mutation_id)
        if self._has_applied_mutation(plan, mutation_id):
            self._session.commit()
            self._session.refresh(task)
            return task

        if command.expected_revision != self._next_revision(plan):
            self._reject("plan_revision_mismatch", "Plan changed; reload before editing")
        if command.refresh_team_snapshot:
            assert task.agent_team_id is not None
            task.team_snapshot = build_team_snapshot(
                self._session,
                workspace_id=task.workspace_id,
                team_id=task.agent_team_id,
            )

        state = _MutationState(
            packages=[dict(package) for package in _raw_packages(plan)],
            steps=self._load_steps(task),
            active_run_step_ids=self._active_run_step_ids(task),
            created_package_ids=set(),
            changed_package_ids=set(),
            new_work_package_ids=set(),
            cancelled_package_ids=set(),
        )
        self._validate_operations(command.operations)
        for operation in command.operations:
            self._apply_operation(state, operation, command.reason)

        active_new_work = {
            package_id
            for package_id in state.new_work_package_ids
            if package_id not in state.cancelled_package_ids
        }
        self._ensure_summary_coverage(
            task,
            state,
            active_new_work,
            command.reason,
        )
        self._require_locked_regions_unchanged(_raw_packages(plan), state.packages)
        next_plan = self._build_next_plan(plan, state.packages, command, actor_user_id, mutation_id)
        self._validate_next_plan(task, next_plan, state)
        self._sync_steps(task, state, command.reason)
        task.project_plan = next_plan

        run = self._enqueue_if_requested(
            task,
            state,
            actor_user_id,
            enqueue_run=enqueue_run,
            queue=queue,
        )
        affected_ids = sorted(state.changed_package_ids | state.created_package_ids)
        preserved_ids = sorted(
            package_id
            for package_id, step in state.steps.items()
            if step.status == TaskStepStatus.COMPLETED.value
        )
        payload = {
            "mutation_id": mutation_id,
            "reason": command.reason,
            "affected_work_package_ids": affected_ids,
            "created_work_package_ids": sorted(state.created_package_ids),
            "cancelled_work_package_ids": sorted(state.cancelled_package_ids),
            "preserved_completed_work_package_ids": preserved_ids,
            "plan_revision": next_plan["plan_revision"],
            "run_id": str(run.id) if run is not None else None,
        }
        TaskMessageAppendService(self._session).append_for_task(
            task,
            message_type="planning.plan_mutated",
            body="Future project work was replanned.",
            payload=payload,
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="task.plan_mutated",
            target_type="task",
            target_id=task.id,
            metadata=payload,
        )
        self._session.commit()
        self._session.refresh(task)
        return task

    def _get_task(self, workspace_id: UUID, task_id: UUID) -> Task | None:
        return self._session.scalar(
            select(Task)
            .where(Task.workspace_id == workspace_id, Task.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _require_task(self, task: Task) -> None:
        if task.agent_team_id is None:
            self._reject("plan_mutation_team_required", "Task is not team-backed")
        if task.status in {"completed", "cancelled"}:
            self._reject("plan_mutation_task_terminal", "Terminal tasks cannot be replanned")
        if not isinstance(task.project_plan, dict):
            self._reject("plan_mutation_plan_required", "Task has no admitted project plan")
        project_plan = task.project_plan
        if project_plan is not None and project_plan.get("strategy") == "agent_planning_pending":
            self._reject(
                "plan_mutation_plan_pending",
                "Agent planning must finish before future work can be mutated",
            )

    def _copy_plan(self, raw_plan: dict[str, object] | None) -> dict[str, object]:
        if not isinstance(raw_plan, dict):
            self._reject("plan_mutation_plan_required", "Task has no admitted project plan")
        assert raw_plan is not None
        return {
            key: list(value) if key == "work_packages" and isinstance(value, list) else value
            for key, value in raw_plan.items()
        }

    def _validate_operations(self, operations: tuple[dict[str, object], ...]) -> None:
        if not operations or len(operations) > MAX_MUTATION_OPERATIONS:
            self._reject(
                "plan_mutation_operation_count",
                "Mutation must include 1 to 32 operations",
            )
        for operation in operations:
            if not isinstance(operation, dict):
                self._reject(
                    "plan_mutation_operation_invalid",
                    "Each mutation operation is an object",
                )
            if not isinstance(operation.get("operation"), str):
                self._reject(
                    "plan_mutation_operation_invalid",
                    "Each operation needs an operation name",
                )

    def _apply_operation(
        self,
        state: _MutationState,
        operation: dict[str, object],
        reason: str,
    ) -> None:
        name = str(operation.get("operation"))
        if name == "add":
            self._add_package(state, operation)
        elif name == "split":
            self._split_package(state, operation)
        elif name == "merge":
            self._merge_packages(state, operation)
        elif name == "cancel":
            self._cancel_package(state, operation, reason)
        elif name == "reassign":
            self._reassign_package(state, operation)
        else:
            self._reject("plan_mutation_operation_invalid", f"Unsupported operation: {name}")

    def _add_package(self, state: _MutationState, operation: dict[str, object]) -> None:
        package = self._validated_package(operation.get("package"))
        package_id = str(package["package_id"])
        self._ensure_mutable_package_identity(package)
        if package_id in self._package_map(state.packages):
            self._reject("plan_mutation_duplicate_package", "Work package ID already exists")
        self._ensure_dependencies_exist(package, state.packages)
        state.packages.append(package)
        state.created_package_ids.add(package_id)
        state.new_work_package_ids.add(package_id)
        state.changed_package_ids.add(package_id)

    def _split_package(self, state: _MutationState, operation: dict[str, object]) -> None:
        source_id = self._operation_package_id(operation, "package_id")
        source = self._mutable_source(state, source_id)
        raw_packages = operation.get("packages")
        if not isinstance(raw_packages, list) or len(raw_packages) < 2:
            self._reject("plan_mutation_split_invalid", "Split requires at least two packages")
        inherited = self._dependencies(source)
        replacements = [
            self._validated_package(
                raw_package,
                inherited_dependencies=tuple(inherited),
            )
            for raw_package in raw_packages
        ]
        for replacement in replacements:
            self._ensure_mutable_package_identity(replacement)
        self._ensure_new_ids(replacements, state.packages, excluded={source_id})
        replacement_ids = [str(package["package_id"]) for package in replacements]
        self._replace_dependency_edges(state, {source_id}, replacement_ids)
        self._mark_cancelled(source, reason="replaced_by_split", replacement_ids=replacement_ids)
        state.cancelled_package_ids.add(source_id)
        state.changed_package_ids.add(source_id)
        source_index = self._package_index(state.packages, source_id)
        for offset, package in enumerate(replacements, start=1):
            state.packages.insert(source_index + offset, package)
            package_id = str(package["package_id"])
            state.created_package_ids.add(package_id)
            state.new_work_package_ids.add(package_id)
            state.changed_package_ids.add(package_id)

    def _merge_packages(self, state: _MutationState, operation: dict[str, object]) -> None:
        source_ids = self._operation_package_ids(operation)
        if len(source_ids) < 2 or len(set(source_ids)) != len(source_ids):
            self._reject(
                "plan_mutation_merge_invalid",
                "Merge requires two or more distinct packages",
            )
        sources = [self._mutable_source(state, package_id) for package_id in source_ids]
        inherited = self._ordered_unique(
            [
                dependency
                for source in sources
                for dependency in self._dependencies(source)
                if dependency not in source_ids
            ]
        )
        target = self._validated_package(
            operation.get("package"),
            inherited_dependencies=tuple(inherited),
        )
        target_id = str(target["package_id"])
        self._ensure_mutable_package_identity(target)
        self._ensure_new_ids([target], state.packages, excluded=set(source_ids))
        self._replace_dependency_edges(state, set(source_ids), [target_id])
        for source_id, source in zip(source_ids, sources, strict=True):
            self._mark_cancelled(source, reason="replaced_by_merge", replacement_ids=[target_id])
            state.cancelled_package_ids.add(source_id)
            state.changed_package_ids.add(source_id)
        insert_index = (
            max(self._package_index(state.packages, source_id) for source_id in source_ids) + 1
        )
        state.packages.insert(insert_index, target)
        state.created_package_ids.add(target_id)
        state.new_work_package_ids.add(target_id)
        state.changed_package_ids.add(target_id)

    def _cancel_package(
        self,
        state: _MutationState,
        operation: dict[str, object],
        reason: str,
    ) -> None:
        root_id = self._operation_package_id(operation, "package_id")
        cascade = operation.get("cascade", False)
        if not isinstance(cascade, bool):
            self._reject("plan_mutation_cancel_invalid", "Cancel cascade must be boolean")
        pending = [root_id]
        cancelled: list[str] = []
        while pending:
            package_id = pending.pop(0)
            package = self._mutable_source(state, package_id)
            dependents: list[str] = []
            for candidate in state.packages:
                candidate_id = str(candidate["package_id"])
                if (
                    self._is_cancelled(candidate)
                    or package_id not in self._dependencies(candidate)
                    or candidate_id in cancelled
                ):
                    continue
                if self._is_platform_package(candidate):
                    self._detach_cancelled_dependency(state, candidate, package_id)
                    continue
                dependents.append(candidate_id)
            if dependents and not cascade:
                self._reject(
                    "plan_mutation_dependency_impact",
                    "Cancel would strand dependent work; set cascade=true",
                )
            pending.extend(dependents)
            self._mark_cancelled(
                package,
                reason=reason,
                replacement_ids=[],
            )
            state.cancelled_package_ids.add(package_id)
            state.changed_package_ids.add(package_id)
            cancelled.append(package_id)

    def _detach_cancelled_dependency(
        self,
        state: _MutationState,
        package: dict[str, object],
        cancelled_package_id: str,
    ) -> None:
        package_id = str(package["package_id"])
        step = state.steps.get(package_id)
        if step is not None:
            if step.id in state.active_run_step_ids or step.status == TaskStepStatus.RUNNING.value:
                self._reject(
                    "plan_mutation_active_side_effect",
                    "Platform work with active side effects cannot be rewired",
                )
            if step.status == TaskStepStatus.COMPLETED.value:
                self._reject(
                    "plan_mutation_completed_dependency",
                    "Completed platform work cannot have its dependency history changed",
                )
            if step.status not in MUTABLE_STEP_STATUSES:
                self._reject(
                    "plan_mutation_step_not_future",
                    "Only future platform work can be rewired",
                )
        package["depends_on"] = [
            dependency
            for dependency in self._dependencies(package)
            if dependency != cancelled_package_id
        ]
        state.changed_package_ids.add(package_id)

    def _reassign_package(self, state: _MutationState, operation: dict[str, object]) -> None:
        package_id = self._operation_package_id(operation, "package_id")
        package = self._mutable_source(state, package_id)
        raw_agent_id = operation.get("assigned_agent_profile_id")
        agent_id = self._uuid(raw_agent_id, "plan_mutation_agent_invalid")
        package["assigned_agent_profile_id"] = str(agent_id)
        state.changed_package_ids.add(package_id)

    def _mutable_source(self, state: _MutationState, package_id: str) -> dict[str, object]:
        package = self._package_map(state.packages).get(package_id)
        if package is None:
            self._reject("plan_mutation_package_not_found", "Work package was not found")
        if self._is_platform_package(package):
            self._reject("plan_mutation_reserved_package", "Platform-owned work cannot be mutated")
        if self._is_cancelled(package):
            self._reject("plan_mutation_package_cancelled", "Cancelled work cannot be mutated")
        step = state.steps.get(package_id)
        if step is None:
            if package_id in state.created_package_ids:
                return package
            self._reject("plan_mutation_step_missing", "Work package has no execution step")
        if step.id in state.active_run_step_ids or step.status == TaskStepStatus.RUNNING.value:
            self._reject(
                "plan_mutation_active_side_effect",
                "Work with active side effects cannot be mutated",
            )
        if step.status == TaskStepStatus.COMPLETED.value:
            self._reject(
                "plan_mutation_completed_immutable",
                "Completed work history is immutable",
            )
        if step.status not in MUTABLE_STEP_STATUSES:
            self._reject("plan_mutation_step_not_future", "Only queued future work can be mutated")
        return package

    def _replace_dependency_edges(
        self,
        state: _MutationState,
        source_ids: set[str],
        replacement_ids: list[str],
    ) -> None:
        for package in state.packages:
            package_id = str(package["package_id"])
            if package_id in source_ids or self._is_cancelled(package):
                continue
            dependencies = self._dependencies(package)
            if not source_ids.intersection(dependencies):
                continue
            step = state.steps.get(package_id)
            if step is not None:
                if (
                    step.id in state.active_run_step_ids
                    or step.status == TaskStepStatus.RUNNING.value
                ):
                    self._reject(
                        "plan_mutation_active_side_effect",
                        "Dependent work with active side effects cannot be rewired",
                    )
                if step.status == TaskStepStatus.COMPLETED.value:
                    self._reject(
                        "plan_mutation_completed_dependency",
                        "Completed work cannot have its dependency history changed",
                    )
                if step.status not in MUTABLE_STEP_STATUSES:
                    self._reject(
                        "plan_mutation_step_not_future",
                        "Only future dependents can be rewired",
                    )
            rewritten: list[str] = []
            for dependency in dependencies:
                values = replacement_ids if dependency in source_ids else [dependency]
                for value in values:
                    if value not in rewritten:
                        rewritten.append(value)
            package["depends_on"] = rewritten
            state.changed_package_ids.add(package_id)

    def _ensure_summary_coverage(
        self,
        task: Task,
        state: _MutationState,
        new_work_package_ids: set[str],
        reason: str,
    ) -> None:
        if not new_work_package_ids:
            return
        summaries = [
            package
            for package in state.packages
            if str(package.get("package_id") or "").startswith("manager-summary")
        ]
        if not summaries:
            return
        summary = summaries[-1]
        summary_id = str(summary["package_id"])
        summary_step = state.steps.get(summary_id)
        if (
            summary_step is not None
            and summary_step.status in MUTABLE_STEP_STATUSES
            and summary_step.id not in state.active_run_step_ids
            and not self._is_cancelled(summary)
        ):
            dependencies = self._dependencies(summary)
            for package_id in sorted(new_work_package_ids):
                if package_id not in dependencies:
                    dependencies.append(package_id)
            summary["depends_on"] = dependencies
            state.changed_package_ids.add(summary_id)
            return

        manager_id = self._uuid(
            summary.get("assigned_agent_profile_id"),
            "plan_mutation_summary_invalid",
        )
        existing_ids = {str(package.get("package_id")) for package in state.packages}
        revision = self._next_revision(task.project_plan)
        candidate_id = f"manager-summary-revision-{revision}"
        suffix = 2
        while candidate_id in existing_ids:
            candidate_id = f"manager-summary-revision-{revision}-{suffix}"
            suffix += 1
        prior_dependencies = [
            dependency
            for dependency in self._dependencies(summary)
            if dependency not in state.cancelled_package_ids
        ]
        if not self._is_cancelled(summary):
            prior_dependencies.insert(0, summary_id)
        dependencies = self._ordered_unique(prior_dependencies + sorted(new_work_package_ids))
        summary_package = {
            "package_id": candidate_id,
            "title": "Manager summary revision",
            "description": "Integrate newly completed future work into the delivery summary.",
            "required_role": "project_manager",
            "required_skills": [],
            "assigned_agent_profile_id": str(manager_id),
            "depends_on": dependencies,
            "expected_artifacts": ["final_delivery"],
            "acceptance_criteria": ["The delivery integrates all newly completed work."],
            "review_policy": {"reviewer": "user", "mode": "final_acceptance"},
            "required_tools": [],
            "required_resource_ids": [],
            "resource_requirements": {},
            "estimated_cost_usd": 0,
            "mutation": {"state": "generated_summary_revision", "reason": reason},
        }
        state.packages.append(summary_package)
        state.created_package_ids.add(candidate_id)
        state.changed_package_ids.add(candidate_id)

    def _require_locked_regions_unchanged(
        self,
        before: list[dict[str, object]],
        after: list[dict[str, object]],
    ) -> None:
        after_by_id = self._package_map(after)
        for node in before:
            if not node.get("locked"):
                continue
            package_id = str(node["package_id"])
            before_dependents = {
                str(item["package_id"])
                for item in before
                if package_id
                in set(self._dependencies(item)) | condition_step_references(item.get("condition"))
            }
            after_dependents = {
                str(item["package_id"])
                for item in after
                if package_id
                in set(self._dependencies(item)) | condition_step_references(item.get("condition"))
            }
            if node != after_by_id.get(package_id) or before_dependents != after_dependents:
                self._reject(
                    "plan_mutation_locked_region",
                    "Locked nodes and their incident edges cannot be changed in a running plan",
                )

    def _build_next_plan(
        self,
        plan: dict[str, object],
        packages: list[dict[str, object]],
        command: TaskPlanMutationCommand,
        actor_user_id: UUID,
        mutation_id: str,
    ) -> dict[str, object]:
        revision = self._next_revision(plan) + 1
        history = plan.get("mutation_history")
        records = list(history) if isinstance(history, list) else []
        records.append(
            {
                "mutation_id": mutation_id,
                "reason": command.reason,
                "actor_user_id": str(actor_user_id),
                "operations": [
                    {
                        "operation": str(operation.get("operation")),
                        "package_id": operation.get("package_id"),
                        "package_ids": operation.get("package_ids", []),
                    }
                    for operation in command.operations
                ],
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        applied_ids = plan.get("applied_mutation_ids")
        applied = (
            [str(value) for value in applied_ids if isinstance(value, str)]
            if isinstance(applied_ids, list)
            else []
        )
        applied.append(mutation_id)
        return {
            **plan,
            "work_packages": packages,
            "plan_revision": revision,
            "mutation_history": records[-MAX_MUTATION_HISTORY:],
            "applied_mutation_ids": applied[-MAX_MUTATION_HISTORY:],
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def _validate_next_plan(
        self,
        task: Task,
        plan: dict[str, object],
        state: _MutationState,
    ) -> None:
        try:
            validate_project_plan(plan, task.team_snapshot)
        except ProjectPlanValidationError as exc:
            self._reject(exc.code, "Mutated project plan failed DAG validation")
        package_map = self._package_map(_raw_packages(plan))
        for package in package_map.values():
            if self._is_cancelled(package):
                continue
            if any(
                self._is_cancelled(package_map[dependency])
                for dependency in self._dependencies(package)
                if dependency in package_map
            ):
                self._reject(
                    "plan_mutation_cancelled_dependency",
                    "Future work cannot depend on cancelled work",
                )
        feasible_packages: list[dict[str, object]] = []
        for package in _raw_packages(plan):
            if self._is_cancelled(package):
                continue
            step = state.steps.get(str(package["package_id"]))
            if step is None or step.status not in FINAL_STEP_STATUSES:
                feasible_packages.append(package)
        if feasible_packages:
            feasibility_plan = {**plan, "work_packages": feasible_packages}
            try:
                PlanFeasibilityService(self._session).validate(
                    task=task,
                    plan=feasibility_plan,
                )
            except ProjectPlanValidationError as exc:
                self._reject(exc.code, "Mutated future work is no longer feasible")

    def _sync_steps(self, task: Task, state: _MutationState, reason: str) -> None:
        materializer = ProjectPlanStepMaterializer(self._session)
        next_order = max((step.order_index for step in state.steps.values()), default=-100) + 100
        for package in state.packages:
            package_id = str(package["package_id"])
            if package_id in state.steps or self._is_cancelled(package):
                continue
            step = materializer.build_step(task, package, order_index=next_order)
            next_order += 100
            self._session.add(step)
            self._session.flush([step])
            state.steps[package_id] = step

        for package in state.packages:
            package_id = str(package["package_id"])
            existing_step = state.steps.get(package_id)
            if existing_step is None:
                continue
            if self._is_cancelled(package):
                if existing_step.status == TaskStepStatus.CANCELLED.value:
                    continue
                if existing_step.status not in MUTABLE_STEP_STATUSES:
                    self._reject(
                        "plan_mutation_step_not_future",
                        "Only future steps can be cancelled",
                    )
                cancellation_dependencies = {
                    **existing_step.dependencies,
                    "mutation": package.get("mutation", {}),
                }
                TaskStepStateService().transition(
                    existing_step,
                    TaskStepStatus.CANCELLED,
                    dependencies=cancellation_dependencies,
                    result_summary=f"Cancelled by future plan mutation: {reason}",
                )
                continue
            if existing_step.status not in MUTABLE_STEP_STATUSES:
                continue
            try:
                after_ids = after_step_ids_for_package(package, state.steps)
            except KeyError:
                self._reject("plan_mutation_missing_dependency", "Dependency step was not found")
            self._update_step_projection(existing_step, package, after_ids)
            if (
                existing_step.status == TaskStepStatus.BLOCKED.value
                and package_id in state.changed_package_ids
            ):
                TaskStepStateService().transition(existing_step, TaskStepStatus.QUEUED)
        self._session.flush()

    def _update_step_projection(
        self,
        step: TaskStep,
        package: dict[str, object],
        after_ids: list[str],
    ) -> None:
        step.assigned_agent_profile_id = self._uuid(
            package.get("assigned_agent_profile_id"),
            "plan_mutation_agent_invalid",
        )
        step.required_role = str(package.get("required_role") or "")
        step.required_skills = _strings(package.get("required_skills"))
        step.expected_artifacts = _strings(package.get("expected_artifacts"))
        step.acceptance_criteria = _strings(package.get("acceptance_criteria"))
        review_policy = package.get("review_policy")
        step.review_policy = dict(review_policy) if isinstance(review_policy, dict) else {}
        step.title = str(package.get("title") or "Work package")
        step.description = str(package.get("description") or "")
        step.dependencies = step_dependencies_for_package(package, after_ids)

    def _enqueue_if_requested(
        self,
        task: Task,
        state: _MutationState,
        actor_user_id: UUID,
        *,
        enqueue_run: bool,
        queue: RedisQueue | None,
    ) -> AgentRun | None:
        if not enqueue_run:
            return None
        if not any(step.status == TaskStepStatus.QUEUED.value for step in state.steps.values()):
            return None
        if self._active_task_run_exists(task):
            return None
        if task.status in {"blocked", "failed"}:
            TaskStateService().reset_to_draft(task)
        from backend.app.orchestration.runs import RunOrchestrationService

        run = RunOrchestrationService(self._session).create_queued_run_for_task(task)
        if run is not None and queue is not None:
            RunOrchestrationService(self._session, queue=queue).enqueue_run(
                run,
                actor_user_id,
            )
        return run

    def _load_steps(self, task: Task) -> dict[str, TaskStep]:
        steps = self._session.scalars(
            select(TaskStep).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
            )
        ).all()
        result: dict[str, TaskStep] = {}
        for step in steps:
            if step.work_package_id is None:
                continue
            if step.work_package_id in result:
                self._reject(
                    "plan_mutation_duplicate_step",
                    "Work package has multiple execution steps",
                )
            result[step.work_package_id] = step
        return result

    def _active_run_step_ids(self, task: Task) -> set[UUID]:
        runs = self._session.scalars(
            select(AgentRun).where(
                AgentRun.workspace_id == task.workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
            )
        ).all()
        active_step_ids: set[UUID] = set()
        side_effecting_statuses = {
            RunStatus.RUNNING.value,
            RunStatus.WAITING_RUNTIME.value,
            RunStatus.WAITING_APPROVAL.value,
        }
        for run in runs:
            if run.task_step_id is None:
                if run.status in side_effecting_statuses:
                    self._reject(
                        "plan_mutation_active_side_effect",
                        "Task-level work with active side effects cannot be replanned",
                    )
                continue
            step = self._session.get(TaskStep, run.task_step_id)
            if step is None:
                if run.status in side_effecting_statuses:
                    self._reject(
                        "plan_mutation_active_side_effect",
                        "Work with an unresolved active side effect cannot be replanned",
                    )
                continue
            if step.status not in FINAL_STEP_STATUSES:
                active_step_ids.add(run.task_step_id)
        return active_step_ids

    def _step_is_open(self, step_id: UUID) -> bool:
        step = self._session.get(TaskStep, step_id)
        return step is not None and step.status not in FINAL_STEP_STATUSES

    def _active_task_run_exists(self, task: Task) -> bool:
        runs = self._session.scalars(
            select(AgentRun).where(
                AgentRun.workspace_id == task.workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
            )
        ).all()
        return any(run.task_step_id is None or self._step_is_open(run.task_step_id) for run in runs)

    def _validated_package(
        self,
        raw_package: object,
        *,
        inherited_dependencies: tuple[str, ...] = (),
    ) -> dict[str, object]:
        if not isinstance(raw_package, dict):
            self._reject("plan_mutation_package_invalid", "Work package must be an object")
        try:
            package = PlannedWork.model_validate(raw_package).model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
        except ValidationError:
            self._reject(
                "plan_mutation_package_invalid",
                "Work package does not match the plan contract",
            )
        if not package["depends_on"] and inherited_dependencies:
            package["depends_on"] = list(inherited_dependencies)
        if not package["review_policy"]:
            package["review_policy"] = {"reviewer": "manager", "mode": "manager_review"}
        return package

    def _ensure_mutable_package_identity(self, package: dict[str, object]) -> None:
        if self._is_platform_package(package):
            self._reject(
                "plan_mutation_reserved_package",
                "Platform-owned work cannot be added or generated by a mutation",
            )

    def _ensure_dependencies_exist(
        self,
        package: dict[str, object],
        packages: list[dict[str, object]],
    ) -> None:
        ids = {str(item.get("package_id")) for item in packages if isinstance(item, dict)}
        if any(dependency not in ids for dependency in self._dependencies(package)):
            self._reject(
                "plan_mutation_missing_dependency",
                "New work depends on an unknown package",
            )

    def _ensure_new_ids(
        self,
        packages: list[dict[str, object]],
        existing_packages: list[dict[str, object]],
        *,
        excluded: set[str],
    ) -> None:
        existing_ids = {str(package.get("package_id")) for package in existing_packages}
        new_ids: set[str] = set()
        for package in packages:
            package_id = str(package["package_id"])
            if package_id in existing_ids or package_id in new_ids or package_id in excluded:
                self._reject("plan_mutation_duplicate_package", "Work package ID already exists")
            new_ids.add(package_id)

    def _package_map(self, packages: list[dict[str, object]]) -> dict[str, dict[str, object]]:
        return {str(package["package_id"]): package for package in packages}

    def _package_index(self, packages: list[dict[str, object]], package_id: str) -> int:
        for index, package in enumerate(packages):
            if str(package.get("package_id")) == package_id:
                return index
        self._reject("plan_mutation_package_not_found", "Work package was not found")

    def _operation_package_id(self, operation: dict[str, object], key: str) -> str:
        value = operation.get(key)
        if not isinstance(value, str) or not value.strip():
            self._reject("plan_mutation_operation_invalid", f"Operation needs {key}")
        return value.strip()

    def _operation_package_ids(self, operation: dict[str, object]) -> list[str]:
        value = operation.get("package_ids")
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            self._reject("plan_mutation_operation_invalid", "Merge needs package_ids")
        return [item.strip() for item in value]

    def _mark_cancelled(
        self,
        package: dict[str, object],
        *,
        reason: str,
        replacement_ids: list[str],
    ) -> None:
        package["mutation"] = {
            "state": "cancelled",
            "reason": reason,
            "replaced_by": replacement_ids,
            "cancelled_at": datetime.now(UTC).isoformat(),
        }

    def _dependencies(self, package: dict[str, object]) -> list[str]:
        value = package.get("depends_on", [])
        if not isinstance(value, list):
            self._reject("plan_mutation_plan_invalid", "Package dependencies must be a list")
        return [str(item) for item in value if isinstance(item, str)]

    def _is_cancelled(self, package: dict[str, object]) -> bool:
        mutation = package.get("mutation")
        return isinstance(mutation, dict) and mutation.get("state") == "cancelled"

    def _is_platform_package(self, package: dict[str, object]) -> bool:
        package_id = str(package.get("package_id") or "")
        review_policy = package.get("review_policy")
        mode = review_policy.get("mode") if isinstance(review_policy, dict) else None
        return (
            package_id == "manager-planning"
            or package_id.startswith("manager-summary")
            or package_id.startswith("executive-")
            or mode in {"final_acceptance", "executive_review"}
        )

    def _mutation_id(self, value: str | None) -> str:
        if value is None:
            return str(uuid4())
        if not value.strip() or len(value) > 120:
            self._reject("plan_mutation_id_invalid", "Mutation ID must be 1 to 120 characters")
        return value.strip()

    def _has_applied_mutation(self, plan: dict[str, object], mutation_id: str) -> bool:
        values = plan.get("applied_mutation_ids")
        return isinstance(values, list) and mutation_id in values

    def _next_revision(self, plan: dict[str, object] | None) -> int:
        if not isinstance(plan, dict):
            return 0
        value = plan.get("plan_revision", 0)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    @staticmethod
    def _ordered_unique(values: list[str] | tuple[str, ...] | object) -> list[str]:
        if not isinstance(values, (list, tuple)):
            return []
        result: list[str] = []
        for value in values:
            if value not in result:
                result.append(value)
        return result

    def _uuid(self, value: object, code: str) -> UUID:
        try:
            return UUID(str(value))
        except (TypeError, ValueError) as exc:
            self._reject(code, "Agent profile ID is invalid")
            raise AssertionError("unreachable") from exc

    @staticmethod
    def _reject(code: str, message: str) -> NoReturn:
        raise TaskPlanMutationError(message, code=code)


def _raw_packages(plan: dict[str, object]) -> list[dict[str, object]]:
    raw_packages = plan.get("work_packages")
    if not isinstance(raw_packages, list) or not raw_packages:
        raise TaskPlanMutationError("Project plan must include work packages", code="plan_invalid")
    packages = [package for package in raw_packages if isinstance(package, dict)]
    if len(packages) != len(raw_packages):
        raise TaskPlanMutationError("Work package must be an object", code="plan_invalid")
    return packages


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


__all__ = [
    "TaskPlanMutationCommand",
    "TaskPlanMutationError",
    "TaskPlanMutationService",
]
