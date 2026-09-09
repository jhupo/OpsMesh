from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.typing import (
    dict_list,
    optional_string,
    string_list,
    string_or_default,
    uuid_or_none,
)
from backend.app.planning.member_matching import MemberMatchingService
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task, TaskMessage, TaskStep

from .task_step_review import is_pm_summary_step

STEP_STATUS_QUEUED = "queued"
STEP_STATUS_COMPLETED = "completed"

AppendTaskMessage = Callable[..., TaskMessage]
CreateNextRuns = Callable[[Task, UUID | None], list[AgentRun]]


@dataclass(slots=True)
class PmFollowUpWorkService:
    session: Session

    def materialize(
        self,
        task: Task,
        *,
        pm_acceptance: dict[str, object],
        requested_by_user_id: UUID | None,
        append_task_message: AppendTaskMessage,
        create_next_runs: CreateNextRuns,
    ) -> list[AgentRun]:
        summary_step = self.latest_completed_pm_summary_step(task)
        if summary_step is None:
            return []

        revision_cycle = self.next_revision_cycle(task)
        follow_up_steps = [
            *self.create_revision_steps(
                task,
                summary_step=summary_step,
                pm_acceptance=pm_acceptance,
                revision_cycle=revision_cycle,
            ),
            *self.create_missing_work_steps(
                task,
                summary_step=summary_step,
                pm_acceptance=pm_acceptance,
                revision_cycle=revision_cycle,
            ),
        ]
        if not follow_up_steps:
            return []

        self.session.flush(follow_up_steps)
        self.create_follow_up_pm_review_step(
            task,
            summary_step=summary_step,
            follow_up_steps=follow_up_steps,
            pm_acceptance=pm_acceptance,
            revision_cycle=revision_cycle,
        )
        append_task_message(
            task_id=task.id,
            workspace_id=task.workspace_id,
            message_type="pm.follow_up_created",
            body=f"PM created {len(follow_up_steps)} follow-up work package(s).",
            task_step_id=summary_step.id,
            agent_profile_id=summary_step.assigned_agent_profile_id,
            payload={
                "decision": pm_acceptance.get("decision"),
                "revision_cycle": revision_cycle,
                "follow_up_step_ids": [str(step.id) for step in follow_up_steps],
                "follow_up_work_package_ids": [step.work_package_id for step in follow_up_steps],
            },
        )
        return create_next_runs(task, requested_by_user_id)

    def create_revision_steps(
        self,
        task: Task,
        *,
        summary_step: TaskStep,
        pm_acceptance: dict[str, object],
        revision_cycle: int,
    ) -> list[TaskStep]:
        revision_requests = dict_list(pm_acceptance.get("revision_requests"))
        steps: list[TaskStep] = []
        for index, request in enumerate(revision_requests, start=1):
            steps.append(
                self.create_revision_step(
                    task,
                    summary_step=summary_step,
                    request=request,
                    revision_cycle=revision_cycle,
                    index=index,
                    pm_acceptance=pm_acceptance,
                )
            )
        return steps

    def create_revision_step(
        self,
        task: Task,
        *,
        summary_step: TaskStep,
        request: dict[str, object],
        revision_cycle: int,
        index: int,
        pm_acceptance: dict[str, object],
    ) -> TaskStep:
        source_step = self.step_for_work_package(
            task,
            optional_string(request.get("work_package_id")),
        )
        assigned_agent_profile_id = uuid_or_none(request.get("assigned_agent_profile_id")) or (
            source_step.assigned_agent_profile_id if source_step is not None else None
        )
        instruction = string_or_default(
            request.get("instruction") or request.get("description"),
            "Revise the referenced work package according to the PM review.",
        )
        source_work_package_id = (
            source_step.work_package_id if source_step is not None else "unknown"
        )
        required_role = optional_string(request.get("required_role")) or (
            source_step.required_role if source_step is not None else None
        ) or "specialist"
        required_skills = string_list(request.get("required_skills")) or (
            source_step.required_skills if source_step is not None else []
        )
        if assigned_agent_profile_id is None:
            assigned_agent_profile_id = self.match_follow_up_agent(
                task,
                required_role=required_role,
                required_skills=required_skills,
            )
        dependency_ids = [str(summary_step.id)]
        if source_step is not None:
            dependency_ids.append(str(source_step.id))
        step = TaskStep(
            workspace_id=task.workspace_id,
            task_id=task.id,
            runtime_space_id=task.runtime_space_id,
            assigned_agent_profile_id=assigned_agent_profile_id,
            work_package_id=f"revision-{source_work_package_id}-{revision_cycle}-{index}",
            required_role=required_role,
            required_skills=required_skills,
            expected_artifacts=string_list(request.get("expected_artifacts"))
            or (source_step.expected_artifacts if source_step is not None else ["revision"]),
            acceptance_criteria=string_list(request.get("acceptance_criteria"))
            or ["The requested revision is addressed without losing prior work."],
            review_policy={"reviewer": "manager", "mode": "revision_review"},
            title=string_or_default(request.get("title"), f"Revision for {source_work_package_id}"),
            description=instruction,
            status=STEP_STATUS_QUEUED,
            order_index=self.next_follow_up_order_index(task),
            dependencies={
                "after_step_ids": dependency_ids,
                "revision_of_work_package_id": source_work_package_id,
                "pm_acceptance_decision": pm_acceptance.get("decision"),
                "revision_request": request,
            },
        )
        self.session.add(step)
        return step

    def create_missing_work_steps(
        self,
        task: Task,
        *,
        summary_step: TaskStep,
        pm_acceptance: dict[str, object],
        revision_cycle: int,
    ) -> list[TaskStep]:
        missing_packages = dict_list(pm_acceptance.get("missing_work_packages"))
        steps: list[TaskStep] = []
        for index, package in enumerate(missing_packages, start=1):
            steps.append(
                self.create_missing_work_step(
                    task,
                    summary_step=summary_step,
                    package=package,
                    revision_cycle=revision_cycle,
                    index=index,
                    pm_acceptance=pm_acceptance,
                )
            )
        return steps

    def create_missing_work_step(
        self,
        task: Task,
        *,
        summary_step: TaskStep,
        package: dict[str, object],
        revision_cycle: int,
        index: int,
        pm_acceptance: dict[str, object],
    ) -> TaskStep:
        required_role = optional_string(package.get("required_role")) or "specialist"
        required_skills = string_list(package.get("required_skills"))
        assigned_agent_profile_id = uuid_or_none(
            package.get("assigned_agent_profile_id")
        ) or self.match_follow_up_agent(
            task,
            required_role=required_role,
            required_skills=required_skills,
        )
        package_id = string_or_default(
            package.get("package_id"),
            f"missing-work-{revision_cycle}-{index}",
        )
        step = TaskStep(
            workspace_id=task.workspace_id,
            task_id=task.id,
            runtime_space_id=task.runtime_space_id,
            assigned_agent_profile_id=assigned_agent_profile_id,
            work_package_id=package_id,
            required_role=required_role,
            required_skills=required_skills,
            expected_artifacts=string_list(package.get("expected_artifacts")) or ["work_summary"],
            acceptance_criteria=string_list(package.get("acceptance_criteria"))
            or ["The missing work is completed and ready for review."],
            review_policy={"reviewer": "manager", "mode": "missing_work_review"},
            title=string_or_default(package.get("title"), "Missing work package"),
            description=string_or_default(
                package.get("description") or package.get("instruction"),
                "Complete the missing work identified by PM review.",
            ),
            status=STEP_STATUS_QUEUED,
            order_index=self.next_follow_up_order_index(task),
            dependencies={
                "after_step_ids": [str(summary_step.id)],
                "pm_acceptance_decision": pm_acceptance.get("decision"),
                "missing_work_package": package,
            },
        )
        self.session.add(step)
        return step

    def create_follow_up_pm_review_step(
        self,
        task: Task,
        *,
        summary_step: TaskStep,
        follow_up_steps: list[TaskStep],
        pm_acceptance: dict[str, object],
        revision_cycle: int,
    ) -> TaskStep:
        step = TaskStep(
            workspace_id=task.workspace_id,
            task_id=task.id,
            runtime_space_id=task.runtime_space_id,
            assigned_agent_profile_id=summary_step.assigned_agent_profile_id,
            work_package_id=f"manager-summary-revision-{revision_cycle}",
            required_role=summary_step.required_role or "project_manager",
            required_skills=summary_step.required_skills or ["review", "synthesis"],
            expected_artifacts=summary_step.expected_artifacts or ["final_delivery"],
            acceptance_criteria=summary_step.acceptance_criteria
            or ["The final answer integrates all completed work packages."],
            review_policy=summary_step.review_policy
            or {
                "reviewer": "user",
                "mode": "final_acceptance",
            },
            title=f"{summary_step.title} revision review",
            description=(
                "Review the completed revision and missing-work outputs, then return a "
                "final PM acceptance decision."
            ),
            status=STEP_STATUS_QUEUED,
            order_index=self.next_follow_up_order_index(task),
            dependencies={
                "after_step_ids": [str(step.id) for step in follow_up_steps],
                "previous_pm_summary_step_id": str(summary_step.id),
                "pm_acceptance_decision": pm_acceptance.get("decision"),
            },
        )
        self.session.add(step)
        self.session.flush([step])
        return step

    def match_follow_up_agent(
        self,
        task: Task,
        *,
        required_role: str,
        required_skills: list[str],
    ) -> UUID | None:
        team_snapshot = task.team_snapshot if isinstance(task.team_snapshot, dict) else {}
        match = MemberMatchingService(self.session).match(
            team_snapshot=team_snapshot,
            required_role=required_role,
            required_skills=required_skills,
            workspace_id=task.workspace_id,
        )
        return match.agent_profile_id if match is not None else None

    def latest_completed_pm_summary_step(self, task: Task) -> TaskStep | None:
        steps = self.session.scalars(
            select(TaskStep)
            .where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.status == STEP_STATUS_COMPLETED,
            )
            .order_by(TaskStep.order_index.desc())
        ).all()
        return next((step for step in steps if is_pm_summary_step(step)), None)

    def step_for_work_package(self, task: Task, work_package_id: str | None) -> TaskStep | None:
        if work_package_id is None:
            return None
        return self.session.scalar(
            select(TaskStep)
            .where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.work_package_id == work_package_id,
            )
            .order_by(TaskStep.order_index.desc())
        )

    def next_follow_up_order_index(self, task: Task) -> int:
        max_order = self.session.scalar(
            select(func.max(TaskStep.order_index)).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
            )
        )
        return int(max_order or 0) + 100

    def next_revision_cycle(self, task: Task) -> int:
        revision_count = self.session.scalar(
            select(func.count(TaskStep.id)).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.work_package_id.like("manager-summary-revision-%"),
            )
        )
        return int(revision_count or 0) + 1
