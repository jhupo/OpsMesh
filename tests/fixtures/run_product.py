from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from opsmesh.agents.execution.contracts import (
    AgentRunRequest,
)
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.orchestration.requests.builder import RunRequestBuilder
from opsmesh.orchestration.runs.authorization.snapshot import RunAuthorizationSnapshotService
from opsmesh.orchestration.runs.eligibility import RunEligibilityService
from opsmesh.orchestration.runs.events import RunEventRecorder
from opsmesh.orchestration.runs.lifecycle import RunLifecycleCallbacks, RunLifecycleService
from opsmesh.orchestration.runs.models import (
    AgentRun,
    authorization_snapshot_fingerprint,
)
from opsmesh.orchestration.runs.resources import (
    RunResourceReservationService,
    release_run_reservations,
)
from opsmesh.orchestration.runs.service import RunOrchestrationService
from opsmesh.orchestration.runs.steps.launcher import RunStepLauncher
from opsmesh.orchestration.runs.steps.scheduling_state import (
    mark_step_scheduling_blocked,
    mark_step_scheduling_runnable,
)
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.orchestration.tasks.state import TaskStatus
from opsmesh.resources.memory.models import (
    WorkspaceMemoryEntry,
    memory_content_fingerprint,
)
from opsmesh.runtime.temporal.contracts import ExecutionRequest
from opsmesh.shared.config import Settings
from tests.fixtures.execution import test_agent_id


def _run_step_launcher(session: Session) -> RunStepLauncher:
    orchestration = RunOrchestrationService(session)
    eligibility = RunEligibilityService(session)
    authorization_snapshots = RunAuthorizationSnapshotService(
        session,
        RunRequestBuilder(session, None),
    )
    return RunStepLauncher(
        session=session,
        events=RunEventRecorder(session),
        build_authorization_snapshot=lambda task, step, profile, agent_snapshot: (
            authorization_snapshots.build_authorization_snapshot(
                task,
                step,
                profile,
                agent_snapshot=agent_snapshot,
            )
        ),
        model_provider_blocked_details=authorization_snapshots.model_provider_blocked_details,
        mark_step_scheduling_blocked=mark_step_scheduling_blocked,
        mark_step_scheduling_runnable=mark_step_scheduling_runnable,
        step_has_active_run=eligibility.step_has_active_run,
        team_scheduler_policy=orchestration._team_scheduler_policy,
    )


def _build_agent_request(
    session: Session,
    run: AgentRun,
    job: ExecutionRequest,
    *,
    settings: Settings | None = None,
) -> AgentRunRequest:
    return RunRequestBuilder(session, settings or Settings(environment="test")).build_agent_request(
        run,
        job,
    )


def _authorized_run_input(
    session: Session,
    task: Task,
    profile: AgentProfile,
    *,
    step: TaskStep | None = None,
    settings: Settings | None = None,
) -> dict[str, object]:
    snapshot = RunAuthorizationSnapshotService(
        session,
        RunRequestBuilder(session, settings or Settings(environment="test")),
    ).build_authorization_snapshot(task, step, profile)
    return {"authorization_snapshot": snapshot}


def _authorization_snapshot(session: Session, **values: object) -> dict[str, object]:
    task = session.get(Task, UUID(str(values["task_id"])))
    assert task is not None
    profile_id = values.get("agent_profile_id")
    profile = session.get(AgentProfile, UUID(str(profile_id))) if profile_id else None
    if profile is not None and profile.workspace_id != task.workspace_id:
        profile = None
    snapshot = RunAuthorizationSnapshotService(
        session, RunRequestBuilder(session, Settings(environment="test"))
    ).build_authorization_snapshot(task, None, profile)
    snapshot.update(values)
    snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
    return snapshot


def _run_lifecycle(session: Session) -> RunLifecycleService:
    orchestration = RunOrchestrationService(session)
    eligibility = RunEligibilityService(session)
    return RunLifecycleService(
        session,
        RunLifecycleCallbacks(
            append_event=RunEventRecorder(session).append_event,
            release_reservations=lambda run, released_at: release_run_reservations(
                session, run, released_at=released_at
            ),
            create_next_runs=lambda task, user_id: orchestration._create_and_enqueue_next_step_runs(
                task,
                requested_by_user_id=user_id,
            ),
            schedule_workspace_steps=lambda workspace_id, user_id: (
                orchestration.schedule_workspace_steps(
                    workspace_id=workspace_id,
                    requested_by_user_id=user_id,
                )
            ),
            task_has_open_team_work=eligibility.task_has_open_team_work,
        ),
    )


def _run_reservations(session: Session) -> RunResourceReservationService:
    return RunResourceReservationService(
        session=session,
        mark_step_scheduling_blocked=mark_step_scheduling_blocked,
        mark_step_scheduling_runnable=mark_step_scheduling_runnable,
    )


def _seed_summary_ready_task(
    session: Session,
    user_id: UUID,
    workspace_id: UUID,
) -> tuple[Task, TaskStep, AgentProfile]:
    manager = AgentProfile(
        workspace_id=workspace_id,
        name="Manager",
        role="manager",
        instructions="Review and summarize.",
        model="manager-model",
    )
    researcher = AgentProfile(
        workspace_id=workspace_id,
        name="Researcher",
        role="researcher",
        instructions="Research.",
        model="researcher-model",
    )
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace_id, user_id),
        workspace_id=workspace_id,
        owner_agent_profile_id=test_agent_id(session, workspace_id),
        created_by_user_id=user_id,
        title="Q2 market analysis",
        description="Produce a concise market analysis.",
        status=TaskStatus.RUNNING.value,
    )
    session.add_all([manager, researcher, task])
    session.flush()
    research_step = TaskStep(
        workspace_id=workspace_id,
        task_id=task.id,
        assigned_agent_profile_id=researcher.id,
        work_package_id="Research-1",
        required_role="Research",
        required_skills=[],
        expected_artifacts=["work_summary"],
        acceptance_criteria=["Research is complete."],
        review_policy={"reviewer": "manager", "mode": "manager_review"},
        title="Research execution",
        description="Collect market facts.",
        status="completed",
        order_index=100,
        dependencies={},
        result_summary="Research completed.",
    )
    summary_step = TaskStep(
        workspace_id=workspace_id,
        task_id=task.id,
        assigned_agent_profile_id=manager.id,
        work_package_id="manager-summary",
        required_role="project_manager",
        required_skills=["review", "synthesis"],
        expected_artifacts=["final_delivery"],
        acceptance_criteria=["The final answer integrates all completed work packages."],
        review_policy={"reviewer": "user", "mode": "final_acceptance"},
        title="Manager summary",
        description="Review specialist outputs and produce final answer.",
        status="queued",
        order_index=1_000,
        dependencies={"after_step_ids": [str(research_step.id)]},
    )
    session.add_all([research_step, summary_step])
    session.flush()
    return task, summary_step, manager


def _semantic_memory_entry(
    *,
    workspace_id: UUID,
    scope_id: UUID,
    title: str,
    content: str,
    tags: list[str],
    metadata: dict[str, object] | None = None,
) -> WorkspaceMemoryEntry:
    return WorkspaceMemoryEntry(
        workspace_id=workspace_id,
        source_type="semantic_memory",
        source_id=str(uuid4()),
        memory_layer="semantic",
        scope_type="team",
        scope_id=str(scope_id),
        memory_key=title.lower().replace(" ", "-"),
        entry_type="semantic_procedure",
        title=title,
        content=content,
        tags=tags,
        visibility_scope="team",
        importance=80,
        status="active",
        content_fingerprint=memory_content_fingerprint(title, content),
        memory_metadata=metadata or {},
    )
