from __future__ import annotations

import io
import json
import tarfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import fakeredis
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from opsmesh.agents.execution.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeInterruption,
    AgentRuntimeResumeState,
    AgentRuntimeStructuredOutput,
)
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.orchestration.approvals.decisions import ApprovalDecisionService
from opsmesh.orchestration.approvals.models import Approval, PendingToolInvocation
from opsmesh.orchestration.planning.attempt_models import TaskPlanningAttempt
from opsmesh.orchestration.runs.models import AgentRun, AgentRunStateSnapshot
from opsmesh.orchestration.runs.service import RunOrchestrationService
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.tasks.collaboration.transfers import (
    TaskTransferCommand,
    TaskTransferDecision,
    TaskTransferService,
)
from opsmesh.orchestration.tasks.contracts import TaskDeliveryDecisionRequest
from opsmesh.orchestration.tasks.delivery.decisions import TaskDeliveryDecisionService
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.orchestration.tasks.state import TaskStatus
from opsmesh.resources.artifacts.models import Artifact
from opsmesh.resources.files.models import WorkspaceFile
from opsmesh.resources.storage.storage import LocalStorage
from opsmesh.runtime.instances.contracts import RuntimeCommandResult
from opsmesh.runtime.instances.models import RuntimeTemplate, WorkspaceRuntime
from opsmesh.runtime.queues.service import RedisQueue
from opsmesh.shared.config import Settings
from opsmesh.shared.redis.keys import RedisKeyBuilder
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.teams.management.models import AgentTeam, AgentTeamMember
from opsmesh.workspaces.projects.models import (
    WorkspaceProject,
    WorkspaceProjectConfigurationVersion,
    WorkspaceProjectFile,
    WorkspaceProjectOutput,
)
from opsmesh.workspaces.projects.snapshots.format import sha256_json
from tests.fixtures.database import flow_database_url
from tests.fixtures.worker import WorkerFlow
from tests.test_worker_run_execution import (
    _patch_portable_types_for_sqlite,
    _seed_workspace,
)


@pytest.fixture(autouse=True)
def approve_reviews(monkeypatch: pytest.MonkeyPatch) -> None:
    from opsmesh.governance.reviews.model_request import ModelRequestReview

    monkeypatch.setattr(
        "opsmesh.governance.reviews.model_request.ModelRequestReviewService.review_request",
        lambda self, **kwargs: ModelRequestReview(
            required=False,
            risk_level="low",
            reasons=["model_request.approved"],
            signals={"reviewer": "llm", "verdict": "approve"},
        ),
    )


@dataclass
class CriticalDocker:
    staged_files: dict[str, bytes] = field(default_factory=dict)
    output_files: dict[str, bytes] = field(default_factory=dict)
    commands: list[list[str]] = field(default_factory=list)

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
        *,
        working_dir: str | None = None,
    ) -> RuntimeCommandResult:
        assert container_id == "critical-container"
        assert timeout_seconds in {30, 60}
        assert working_dir == "/"
        self.commands.append(command)
        return RuntimeCommandResult(exit_code=0, stdout="", stderr="")

    def copy_archive_to_container(
        self,
        container_id: str,
        destination_path: str,
        archive: bytes,
        timeout_seconds: int,
    ) -> None:
        assert container_id == "critical-container"
        assert destination_path == "/workspace"
        assert timeout_seconds == 60
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            for member in bundle.getmembers():
                if member.isfile():
                    stream = bundle.extractfile(member)
                    assert stream is not None
                    self.staged_files[member.name] = stream.read()

    def copy_file_from_container(
        self,
        container_id: str,
        source_path: str,
        max_bytes: int,
        timeout_seconds: int,
    ) -> bytes | None:
        assert container_id == "critical-container"
        assert timeout_seconds == 60
        content = self.output_files.get(source_path)
        if content is not None:
            assert len(content) <= max_bytes
        return content


def test_critical_agent_workflow_plan_read_approval_restart_handoff_and_acceptance(
    tmp_path: Path,
) -> None:
    _patch_portable_types_for_sqlite()
    session = _session()
    owner, workspace = _seed_workspace(session)
    settings = Settings(
        environment="test",
        storage_root=str(tmp_path / "storage"),
        credential_encryption_secret="change-me-credential-encryption-secret",
        credential_encryption_key_id="local",
    )
    storage = LocalStorage(str(tmp_path / "storage"))
    docker = CriticalDocker()
    planner = AgentProfile(
        workspace_id=workspace.id,
        name="Planner",
        role="project_manager",
        model="gpt-4.1",
        capabilities={"resource_ids": []},
    )
    builder = AgentProfile(
        workspace_id=workspace.id,
        name="Builder",
        role="builder",
        model="gpt-4.1",
        tool_policy={"allowed_tools": ["read_workspace_file", "write_artifact"]},
        capabilities={"resource_ids": []},
    )
    handoff_target = AgentProfile(
        workspace_id=workspace.id,
        name="Reviewer",
        role="reviewer",
        model="gpt-4.1",
        capabilities={"resource_ids": []},
    )
    session.add_all([planner, builder, handoff_target])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Critical workflow team",
        manager_agent_profile_id=planner.id,
    )
    session.add(team)
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=planner.id,
                team_role="project_manager",
                order_index=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=builder.id,
                team_role="builder",
                order_index=2,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=handoff_target.id,
                team_role="reviewer",
                order_index=3,
            ),
        ]
    )
    template = RuntimeTemplate(
        name="critical-workflow",
        image="example.invalid/critical@sha256:" + "1" * 64,
        created_at=datetime.now(UTC),
    )
    session.add(template)
    session.flush()
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_template_id=template.id,
        runtime_provider="cloud_docker",
        runtime_type="docker",
        execution_mode="persistent",
        name="critical-runtime",
        status="running",
        connection_status="online",
        docker_container_id="critical-container",
        limits={
            "cpu_count": 1,
            "memory_mb": 256,
            "disk_mb": 256,
            "timeout_seconds": 60,
            "max_output_bytes": 256_000,
            "max_processes": 64,
            "max_concurrent_executions": 4,
        },
        network_policy={"mode": "none"},
        capabilities={"isolation": {"workspace_mount": {"target": "/workspace", "mode": "rw"}}},
    )
    content = b"project input"
    source_file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=owner.id,
        filename="spec.txt",
        content_type="text/plain",
        size_bytes=len(content),
        checksum_sha256=sha256(content).hexdigest(),
        storage_key=f"workspaces/{workspace.id}/files/spec.txt",
        status="active",
    )
    project = WorkspaceProject(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Critical project",
        slug="critical-project",
        input_path="inputs",
        work_path="work",
        output_path="outputs",
        configuration={"language": "python"},
        configuration_version=1,
        status="active",
    )
    session.add_all([runtime, source_file, project])
    session.flush()
    session.add_all(
        [
            WorkspaceProjectConfigurationVersion(
                workspace_id=workspace.id,
                project_id=project.id,
                version=1,
                configuration=project.configuration,
                checksum_sha256=sha256_json(project.configuration),
                created_by_user_id=owner.id,
                change_summary="Initial configuration",
            ),
            WorkspaceProjectFile(
                workspace_id=workspace.id,
                project_id=project.id,
                workspace_file_id=source_file.id,
                project_path="inputs/spec.txt",
                version=1,
                access_mode="read_only",
                status="active",
            ),
            WorkspaceProjectOutput(
                workspace_id=workspace.id,
                project_id=project.id,
                project_path="outputs/report.json",
                artifact_type="report",
                content_type="application/json",
                required=True,
                max_bytes=1024,
                status="active",
            ),
        ]
    )
    session.flush()
    from opsmesh.capabilities.references.models import CapabilityResource

    runtime_resource = CapabilityResource(
        workspace_id=workspace.id,
        key="critical-runtime",
        name="Critical runtime",
        resource_type="runtime",
        access_mode="execute",
        locator={"workspace_runtime_id": str(runtime.id)},
    )
    file_resource = CapabilityResource(
        workspace_id=workspace.id,
        key="critical-files",
        name="Critical project files",
        resource_type="file_collection",
        access_mode="read",
        locator={"file_ids": [str(source_file.id)]},
    )
    session.add_all([runtime_resource, file_resource])
    session.flush()
    for profile in (planner, builder, handoff_target):
        profile.capabilities = {"resource_ids": [str(runtime_resource.id), str(file_resource.id)]}
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, owner.id),
        workspace_id=workspace.id,
        agent_team_id=team.id,
        owner_agent_profile_id=planner.id,
        created_by_user_id=owner.id,
        workspace_project_id=project.id,
        title="Produce a reviewed report",
        description="Read the specification and produce the report.",
        status=TaskStatus.QUEUED.value,
        input={"planning_mode": "agent"},
    )
    session.add(task)
    session.flush()
    storage.write(source_file.storage_key, content)
    session.commit()

    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("opsmesh"),
        queue_name="agent_runs",
        blocking_timeout_seconds=0,
    )
    orchestration = RunOrchestrationService(session, queue)
    planning_run = orchestration.create_queued_run_for_task(task)
    assert planning_run is not None
    assert planning_run.runtime_id == runtime.id
    orchestration.enqueue_run(planning_run, owner.id)
    session.commit()

    class PlannerRunner:
        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            docker.output_files[f"/workspace/runs/{request.context.run_id}/outputs/report.json"] = (
                b'{"status":"planning"}'
            )
            return AgentRunResult(
                final_output="plan admitted",
                structured_output=AgentRuntimeStructuredOutput(
                    value={
                        "objective": task.title,
                        "work_packages": [
                            {
                                "package_id": "build-report",
                                "title": "Build report",
                                "description": "Read the spec and write the report.",
                                "required_role": "builder",
                                "required_skills": [],
                                "assigned_agent_profile_id": str(builder.id),
                                "depends_on": [],
                                "expected_artifacts": ["report"],
                                "acceptance_criteria": ["Report is produced."],
                            },
                            {
                                "package_id": "report-review",
                                "title": "Review report",
                                "assigned_agent_profile_id": str(planner.id),
                                "depends_on": ["build-report"],
                                "review_policy": {"mode": "final_acceptance", "reviewer": "user"},
                            },
                        ],
                    },
                    schema_name="task_plan",
                    schema_version="3",
                    validated=True,
                ),
            )

    handler = WorkerFlow(
        session,
        queue,
        agent_runner=PlannerRunner(),
        settings=settings,
        runtime_docker_client=docker,
    )
    assert handler.process_next()
    session.refresh(task)
    assert task.project_plan is not None
    planning_attempt = session.scalar(
        select(TaskPlanningAttempt).where(TaskPlanningAttempt.task_id == task.id)
    )
    assert planning_attempt is not None
    assert planning_attempt.status == "completed", {
        "validation_errors": planning_attempt.validation_errors,
        "run_error": planning_run.error,
        "run_status": planning_run.status,
    }
    build_step = session.scalar(
        select(TaskStep).where(
            TaskStep.workspace_id == workspace.id,
            TaskStep.work_package_id == "build-report",
        )
    )
    summary_step = session.scalar(
        select(TaskStep).where(
            TaskStep.workspace_id == workspace.id,
            TaskStep.work_package_id == "report-review",
        )
    )
    assert build_step is not None and summary_step is not None
    build_run = session.scalar(select(AgentRun).where(AgentRun.task_step_id == build_step.id))
    assert build_run is not None and build_run.status == RunStatus.QUEUED.value

    class InterruptingBuilder:
        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            assert request.tool_executor is not None
            read = await request.tool_executor.execute_tool(
                context=request.context,
                tool_name="read_workspace_file",
                arguments={"file_id": str(source_file.id)},
            )
            assert read.status == "completed"
            assert read.output is not None and read.output["content"] == "project input"
            return AgentRunResult(
                final_output="",
                resume_state=AgentRuntimeResumeState(
                    provider="openai_agents",
                    serialized_state='{"turn":"waiting-for-approval"}',
                    schema_version="1.0",
                    sdk_version="test",
                ),
                interruptions=(
                    AgentRuntimeInterruption(
                        tool_call_id="critical-write",
                        tool_name="write_artifact",
                        tool_kind="product",
                        arguments={"filename": "tool.txt", "content": "approved"},
                        policy_decision={"decision": "require_approval", "risk_level": "high"},
                    ),
                ),
            )

    assert WorkerFlow(
        session,
        queue,
        agent_runner=InterruptingBuilder(),
        settings=settings,
        runtime_docker_client=docker,
    ).process_next()
    assert build_run.status == RunStatus.WAITING_APPROVAL.value
    summary_step.status = "blocked"
    session.commit()
    bind = session.get_bind()
    # Capture immutable identities before the old unit of work is discarded.
    detached_owner_id, detached_build_run_id = owner.id, build_run.id
    detached_planner_id = planner.id
    detached_workspace_id, detached_task_id = workspace.id, task.id
    detached_handoff_target_id = handoff_target.id
    session.close()
    restarted = sessionmaker(bind=bind, expire_on_commit=False)()
    restart_queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("opsmesh"),
        queue_name="agent_runs",
        blocking_timeout_seconds=0,
    )
    approval = restarted.scalar(
        select(Approval).where(Approval.agent_run_id == detached_build_run_id)
    )
    assert approval is not None
    ApprovalDecisionService(
        restarted,
        restart_queue,
        SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
        ),
    ).approve(approval, detached_owner_id, "approved after worker restart")

    class ResumingBuilder:
        calls = 0

        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            self.calls += 1
            assert request.resume_state is not None
            assert request.approval_decisions[0].tool_call_id == "critical-write"
            assert request.approval_decisions[0].status == "approved"
            assert request.tool_executor is not None
            arguments = {"filename": "tool.txt", "content": "approved"}
            first = await request.tool_executor.execute_tool(
                context=request.context,
                tool_name="write_artifact",
                arguments=arguments,
                tool_call_id="critical-write",
                approval_granted=True,
            )
            replay = await request.tool_executor.execute_tool(
                context=request.context,
                tool_name="write_artifact",
                arguments=arguments,
                tool_call_id="critical-write",
                approval_granted=True,
            )
            assert first.status == "completed"
            assert replay.output == first.output
            docker.output_files[f"/workspace/runs/{request.context.run_id}/outputs/report.json"] = (
                b'{"status":"approved"}'
            )
            return AgentRunResult(final_output="builder completed")

    resuming_builder = ResumingBuilder()
    assert WorkerFlow(
        restarted,
        restart_queue,
        agent_runner=resuming_builder,
        settings=settings,
        runtime_docker_client=docker,
    ).process_next()
    restarted_build_run = restarted.get(AgentRun, detached_build_run_id)
    assert restarted_build_run is not None
    assert restarted_build_run.status == RunStatus.COMPLETED.value
    invocation = restarted.scalar(
        select(PendingToolInvocation).where(
            PendingToolInvocation.agent_run_id == detached_build_run_id
        )
    )
    state = restarted.scalar(
        select(AgentRunStateSnapshot).where(
            AgentRunStateSnapshot.agent_run_id == detached_build_run_id
        )
    )
    assert invocation is not None and invocation.status == "completed"
    assert invocation.attempt_count == 1
    assert state is not None and state.status == "consumed"
    assert resuming_builder.calls == 1

    transfer_service = TaskTransferService(restarted)
    first_transfer = transfer_service.request_transfer(
        workspace_id=detached_workspace_id,
        task_id=detached_task_id,
        actor_user_id=detached_owner_id,
        command=TaskTransferCommand(
            target_agent_profile_id=detached_handoff_target_id,
            source_agent_profile_id=detached_planner_id,
            reason="Reviewer takes ownership for final handoff.",
            idempotency_key="critical-handoff-to-reviewer",
        ),
    )
    assert first_transfer is not None
    first_transfer = transfer_service.accept_transfer(
        workspace_id=detached_workspace_id,
        task_id=detached_task_id,
        transfer_id=first_transfer.id,
        actor_user_id=detached_owner_id,
        decision=TaskTransferDecision(reason="Reviewer accepted the handoff."),
    )
    assert first_transfer is not None and first_transfer.status == "accepted"
    second_transfer = transfer_service.request_transfer(
        workspace_id=detached_workspace_id,
        task_id=detached_task_id,
        actor_user_id=detached_owner_id,
        command=TaskTransferCommand(
            target_agent_profile_id=detached_planner_id,
            source_agent_profile_id=detached_handoff_target_id,
            reason="Return ownership to the project manager for acceptance.",
            idempotency_key="critical-handoff-back-to-manager",
        ),
    )
    assert second_transfer is not None
    second_transfer = transfer_service.accept_transfer(
        workspace_id=detached_workspace_id,
        task_id=detached_task_id,
        transfer_id=second_transfer.id,
        actor_user_id=detached_owner_id,
        decision=TaskTransferDecision(reason="Manager resumed final acceptance."),
    )
    assert second_transfer is not None and second_transfer.status == "accepted"
    restarted_task = restarted.get(Task, detached_task_id)
    assert restarted_task is not None
    assert restarted_task.owner_agent_profile_id == detached_planner_id

    summary_step = restarted.get(TaskStep, summary_step.id)
    assert summary_step is not None
    summary_step.status = "queued"
    restarted.commit()
    scheduled = RunOrchestrationService(restarted, restart_queue).schedule_workspace_steps(
        workspace_id=detached_workspace_id,
        requested_by_user_id=detached_owner_id,
    )
    assert len(scheduled) == 1
    restarted.commit()

    class ManagerRunner:
        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            docker.output_files[f"/workspace/runs/{request.context.run_id}/outputs/report.json"] = (
                b'{"status":"manager-approved"}'
            )
            return AgentRunResult(
                final_output=json.dumps(
                    {
                        "decision": "approved",
                        "summary": "Manager integrated the reviewed report.",
                        "reasons": [],
                    }
                ),
                structured_output=AgentRuntimeStructuredOutput(
                    value={
                        "decision": "approved",
                        "summary": "Manager integrated the reviewed report.",
                        "reasons": [],
                    },
                    schema_name="pm_acceptance",
                    validated=True,
                ),
            )

    assert WorkerFlow(
        restarted,
        restart_queue,
        agent_runner=ManagerRunner(),
        settings=settings,
        runtime_docker_client=docker,
    ).process_next()
    restarted_task = restarted.get(Task, detached_task_id)
    assert restarted_task is not None
    delivery = TaskDeliveryDecisionService(restarted, queue=restart_queue).apply_decision(
        workspace_id=detached_workspace_id,
        task_id=detached_task_id,
        actor_user_id=detached_owner_id,
        request=TaskDeliveryDecisionRequest(
            action="approve",
            summary="User accepted the complete delivery.",
            finalize=True,
            override=True,
            override_reason=(
                "The manager-approved artifacts were verified during the acceptance run."
            ),
        ),
    )
    assert delivery is not None
    assert delivery["details"]["finalization"]["status"] == "finalized", delivery
    assert restarted_task.status == TaskStatus.COMPLETED.value
    assert restarted_task.final_output is not None
    artifacts = restarted.scalars(
        select(Artifact).where(
            Artifact.workspace_id == detached_workspace_id, Artifact.task_id == detached_task_id
        )
    ).all()
    assert {artifact.artifact_type for artifact in artifacts} >= {"file", "report"}
    assert {event.action for event in restarted.scalars(select(AuditEvent)).all()} >= {
        "task.transfer.accepted",
        "project.inputs_staged",
        "project.outputs_harvested",
    }


def _session() -> Session:
    from sqlalchemy import create_engine

    engine = create_engine(
        flow_database_url(),
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    from opsmesh.shared.db.base import Base

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()
