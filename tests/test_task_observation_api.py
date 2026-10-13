"""Workspace-scoped product API verification on PostgreSQL."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.orchestration.runs.models import AgentRun, RunEvent
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.tasks.delivery.correction_diagnostics import (
    TaskCorrectionDiagnosticsService,
)
from opsmesh.orchestration.tasks.event_store import TaskEventStore
from opsmesh.orchestration.tasks.message_append import (
    TASK_MESSAGE_CREATED_EVENT_TYPE,
    TaskMessageAppendService,
)
from opsmesh.orchestration.tasks.models import Task, TaskEventRecord, TaskMessage, TaskStep
from opsmesh.orchestration.tasks.observation.execution import TaskExecutionDiagnosticsService
from opsmesh.orchestration.tasks.observation.service import TaskObservationService
from opsmesh.orchestration.tasks.observation.timeline import TaskTimelineService
from opsmesh.orchestration.tasks.state import TaskStatus
from opsmesh.resources.artifacts.models import Artifact
from opsmesh.teams.management.models import AgentTeam
from tests.fixtures.execution import test_agent_id
from tests.fixtures.workspace_api import _client, _headers, _seed_workspace, _sse_data_events


def test_agent_profile_response_redacts_sensitive_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Sensitive Agent",
            "role": "researcher",
            "model_settings": {
                "temperature": 0.2,
                "api_key": "sk-agent",
                "provider": {"base_url": "https://router.example.test/private"},
            },
            "capabilities": {"headers": {"authorization": "Bearer hidden"}},
            "tool_policy": {"token": "tool-token"},
            "runtime_policy": {"access_token": "sandbox-secret"},
        },
    )
    listed = client.get(f"/api/v1/workspaces/{workspace.id}/agents", headers=_headers(owner.id))
    assert created.status_code == 201
    body = created.json()
    assert body["model_settings"] == {
        "temperature": 0.2,
        "api_key": "[redacted]",
        "provider": {"base_url": "[redacted]"},
    }
    assert body["capabilities"] == {"headers": "[redacted]"}
    assert body["tool_policy"] == {"token": "[redacted]"}
    assert body["runtime_policy"] == {"access_token": "[redacted]"}
    assert listed.status_code == 200
    serialized = str(listed.json())
    assert "sk-agent" not in serialized
    assert "router.example.test/private" not in serialized
    assert "sandbox-secret" not in serialized


def test_task_messages_api_lists_filters_and_enforces_workspace_scope() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other@example.com", slug="other-space"
    )
    task = Task(workspace_id=workspace.id, created_by_user_id=owner.id, title="Task")
    session.add(task)
    session.flush()
    session.add_all(
        [
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                message_type="step.started",
                sequence=1,
                body="Started",
                payload={
                    "work_package_id": "research",
                    "api_key": "sk-hidden",
                    "nested": {"authorization": "Bearer hidden"},
                },
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                message_type="step.completed",
                sequence=2,
                body="Completed",
                payload={"work_package_id": "research"},
            ),
        ]
    )
    session.commit()
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/messages", headers=_headers(owner.id)
    )
    filtered = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/messages?message_type=step.completed",
        headers=_headers(owner.id),
    )
    foreign = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/tasks/{task.id}/messages",
        headers=_headers(other_owner.id),
    )
    assert listed.status_code == 200
    assert [item["sequence"] for item in listed.json()["items"]] == [1, 2]
    assert listed.json()["items"][0]["payload"] == {
        "work_package_id": "research",
        "api_key": "[redacted]",
        "nested": {"authorization": "[redacted]"},
    }
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["message_type"] == "step.completed"
    assert foreign.status_code == 403


def test_task_live_status_api_returns_active_runs_and_message_cursor() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    agent = AgentProfile(workspace_id=workspace.id, name="Researcher", role="researcher")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Live task",
        status=TaskStatus.RUNNING.value,
        priority=7,
    )
    session.add_all([agent, task])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        title="Research",
        work_package_id="research",
        status="running",
        order_index=1,
    )
    session.add(step)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        agent_profile_id=agent.id,
        status=RunStatus.RUNNING.value,
        input={},
        model="gpt-test",
        started_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    session.add_all(
        [
            RunEvent(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                event_type="run.started",
                sequence=1,
                message="Started",
                event_metadata={"api_key": "sk-hidden"},
                created_at=datetime.now(UTC),
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=step.id,
                agent_run_id=run.id,
                agent_profile_id=agent.id,
                message_type="step.started",
                sequence=1,
                body="Started",
                payload={"token": "hidden"},
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=step.id,
                agent_run_id=run.id,
                agent_profile_id=agent.id,
                message_type="agent.progress",
                sequence=2,
                body="Working",
                payload={"progress": "drafting"},
            ),
        ]
    )
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/live-status?after_sequence=1&message_limit=10",
        headers=_headers(owner.id),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["status"] == TaskStatus.RUNNING.value
    assert payload["summary"]["active_run_count"] == 1
    assert payload["summary"]["latest_message_sequence"] == 2
    assert payload["summary"]["poll_after_seconds"] == 1
    assert payload["steps"][0]["assigned_agent"]["name"] == "Researcher"
    assert payload["active_runs"][0]["latest_event"]["metadata"]["api_key"] == "[redacted]"
    assert [message["sequence"] for message in payload["recent_messages"]] == [2]
    assert payload["recent_messages"][0]["agent"]["role"] == "researcher"


def test_task_live_status_scopes_participants_and_includes_message_only_agents() -> None:
    from opsmesh.orchestration.tasks.observation.live_status import TaskLiveStatusService

    _, session = _client()
    _, workspace = _seed_workspace(session, role="owner")
    _, other = _seed_workspace(
        session, role="owner", email="live-other@example.com", slug="live-other"
    )
    local_agent = AgentProfile(workspace_id=workspace.id, name="Message author", role="researcher")
    foreign_agent = AgentProfile(workspace_id=other.id, name="Private agent", role="researcher")
    session.add_all([local_agent, foreign_agent])
    session.flush()
    agents = TaskLiveStatusService(session)._agents_by_id(
        workspace.id,
        [],
        [TaskStep(assigned_agent_profile_id=foreign_agent.id)],
        [TaskMessage(agent_profile_id=local_agent.id), TaskMessage(agent_profile_id=None)],
    )
    assert agents == {local_agent.id: local_agent}


def test_task_event_stream_returns_redacted_snapshot() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Streaming task",
        status=TaskStatus.COMPLETED.value,
    )
    session.add(task)
    session.flush()
    session.add(
        TaskMessage(
            workspace_id=workspace.id,
            task_id=task.id,
            message_type="step.completed",
            sequence=1,
            body="Done",
            payload={"authorization": "Bearer hidden"},
        )
    )
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/events/stream?once=true",
        headers=_headers(owner.id),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: task.snapshot" in response.text
    assert "[redacted]" in response.text
    assert "Bearer hidden" not in response.text


def test_task_event_stream_reads_committed_message_events_from_postgres() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Streaming task",
        status=TaskStatus.RUNNING.value,
    )
    session.add(task)
    session.commit()
    message = TaskMessageAppendService(session).append_for_task(
        task,
        message_type="agent.progress",
        body="Sensitive body should not be streamed in the bus event",
        payload={"authorization": "Bearer hidden", "progress": "drafting"},
    )
    session.commit()
    outbox_event = session.scalar(select(TaskEventRecord).where(TaskEventRecord.task_id == task.id))
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/events/stream?after_sequence=1&event_cursor=0&once=true",
        headers=_headers(owner.id),
    )
    assert response.status_code == 200
    assert outbox_event is not None
    assert "event: task.event" in response.text
    assert TASK_MESSAGE_CREATED_EVENT_TYPE in response.text
    assert str(message.id) in response.text
    task_events = _sse_data_events(response.text, "task.event")
    assert len(task_events) == 1
    task_event = task_events[0]
    assert task_event["id"] == task_event["stream"]["event_cursor"]
    assert task_event["event_id"] == str(outbox_event.event_id)
    assert task_event["payload"]["event_id"] == str(outbox_event.event_id)
    assert "Sensitive body" not in response.text
    assert "Bearer hidden" not in response.text


def test_task_event_stream_replays_committed_events_by_durable_cursor() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Streaming replay task",
        status=TaskStatus.RUNNING.value,
    )
    session.add(task)
    session.commit()
    event = TaskEventStore(session).append(
        workspace_id=workspace.id,
        task_id=task.id,
        event_type=TASK_MESSAGE_CREATED_EVENT_TYPE,
        payload={
            "message_id": str(uuid4()),
            "message_type": "agent.progress",
            "sequence": 7,
            "api_key": "sk-hidden",
            "nested": {"authorization": "Bearer hidden"},
        },
    )
    session.commit()
    event_id = str(event.event_id)
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/events/stream?event_cursor=0&once=true",
        headers=_headers(owner.id),
    )
    resumed = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/events/stream?event_cursor={event.event_id}&once=true",
        headers=_headers(owner.id),
    )
    assert response.status_code == 200
    assert "event: task.event" in response.text
    assert event_id in response.text
    assert TASK_MESSAGE_CREATED_EVENT_TYPE in response.text
    task_events = _sse_data_events(response.text, "task.event")
    assert len(task_events) == 1
    task_event = task_events[0]
    assert task_event["event_id"] == event_id
    assert task_event["id"] == str(event.event_id)
    assert task_event["stream"]["event_cursor"] == str(event.event_id)
    assert task_event["payload"]["api_key"] == "[redacted]"
    assert task_event["payload"]["nested"]["authorization"] == "[redacted]"
    assert "outbox_id" not in task_event
    assert resumed.status_code == 200
    assert _sse_data_events(resumed.text, "task.event") == []
    assert "[redacted]" in response.text
    assert "sk-hidden" not in response.text
    assert "Bearer hidden" not in response.text


def test_run_api_redacts_sensitive_payloads() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    run = AgentRun(
        workspace_id=workspace.id,
        status=RunStatus.FAILED.value,
        input={"prompt": "draft", "api_key": "sk-hidden"},
        output={
            "result": {"token": "hidden-token"},
            "tool_payload": {
                "provider": "openai_agents",
                "mode": "tool_result",
                "items": [
                    {
                        "role": "tool",
                        "base_url": "https://router.example.test/private",
                        "headers": {"authorization": "Bearer hidden"},
                    }
                ],
            },
        },
        error={"message": "failed", "authorization": "Bearer hidden"},
    )
    session.add(run)
    session.flush()
    event = RunEvent(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        event_type="run.failed",
        sequence=1,
        message="Failed",
        event_metadata={"secret": "hidden", "safe": "ok"},
        created_at=datetime.now(UTC),
    )
    session.add(event)
    session.commit()
    runs = client.get(f"/api/v1/workspaces/{workspace.id}/runs", headers=_headers(owner.id))
    events = client.get(
        f"/api/v1/workspaces/{workspace.id}/runs/{run.id}/events", headers=_headers(owner.id)
    )
    assert runs.status_code == 200
    payload = runs.json()["items"][0]
    assert payload["input"]["api_key"] == "[redacted]"
    assert payload["output"]["result"]["token"] == "[redacted]"
    assert payload["output"]["tool_payload"]["items"][0]["base_url"] == "[redacted]"
    assert payload["output"]["tool_payload"]["items"][0]["headers"] == "[redacted]"
    assert payload["error"]["authorization"] == "[redacted]"
    assert "router.example.test/private" not in str(payload)
    assert "Bearer hidden" not in str(payload)
    assert events.status_code == 200
    assert events.json()["items"][0]["event_metadata"] == {"secret": "[redacted]", "safe": "ok"}


def test_audit_event_api_redacts_sensitive_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    session.add(
        AuditEvent(
            workspace_id=workspace.id,
            actor_type="user",
            actor_id=str(owner.id),
            user_id=owner.id,
            action="model_provider.used",
            target_type="agent_run",
            target_id=str(uuid4()),
            audit_metadata={
                "api_key": "sk-hidden",
                "provider": {"base_url": "https://router.example.test/private"},
                "safe": "visible",
            },
            created_at=datetime.now(UTC),
        )
    )
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/audit-events?action=model_provider.used",
        headers=_headers(owner.id),
    )
    assert response.status_code == 200
    metadata = response.json()["items"][0]["audit_metadata"]
    assert metadata == {
        "api_key": "[redacted]",
        "provider": {"base_url": "[redacted]"},
        "safe": "visible",
    }
    assert "sk-hidden" not in str(metadata)
    assert "router.example.test/private" not in str(metadata)


def test_task_execution_diagnostics_explains_assignments_dependencies_and_blockers() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other-exec@example.com", slug="other-exec"
    )
    active_agent_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Developer", "role": "developer"},
    )
    inactive_agent_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Reviewer", "role": "reviewer"},
    )
    inactive_agent = session.get(AgentProfile, UUID(inactive_agent_response.json()["id"]))
    assert inactive_agent is not None
    inactive_agent.status = "inactive"
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Build dashboard",
        status=TaskStatus.RUNNING.value,
        priority=4,
        domain_type="software",
        input={"api_key": "sk-hidden"},
    )
    session.add(task)
    session.flush()
    completed_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=UUID(active_agent_response.json()["id"]),
        work_package_id="design",
        required_role="designer",
        title="Design",
        status="completed",
        order_index=10,
        result_summary="Design complete",
    )
    session.add(completed_step)
    session.flush()
    runnable_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=UUID(active_agent_response.json()["id"]),
        work_package_id="build",
        required_role="developer",
        title="Build",
        status="queued",
        order_index=20,
        dependencies={"after_step_ids": [str(completed_step.id)]},
    )
    session.add(runnable_step)
    session.flush()
    blocked_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=UUID(inactive_agent_response.json()["id"]),
        work_package_id="review",
        required_role="reviewer",
        title="Review",
        status="queued",
        order_index=30,
        dependencies={
            "after_step_ids": [str(runnable_step.id)],
            "blocked_reason": "workspace_run_quota_exceeded",
            "token": "hidden-token",
        },
    )
    unassigned_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="release",
        required_role="release_manager",
        title="Release",
        status="queued",
        order_index=40,
        dependencies={"after_step_ids": [str(uuid4())]},
    )
    session.add_all([blocked_step, unassigned_step])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=blocked_step.id,
        agent_profile_id=UUID(inactive_agent_response.json()["id"]),
        status=RunStatus.QUEUED.value,
        input={},
        error={"api_key": "sk-run"},
    )
    session.add(run)
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/execution-diagnostics",
        headers=_headers(owner.id),
    )
    foreign_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/tasks/{task.id}/execution-diagnostics",
        headers=_headers(other_owner.id),
    )
    missing_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{uuid4()}/execution-diagnostics",
        headers=_headers(owner.id),
    )
    raw_diagnostics = TaskExecutionDiagnosticsService(session).get_diagnostics(
        workspace_id=workspace.id, task_id=task.id
    )
    assert active_agent_response.status_code == 201
    assert inactive_agent_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == str(task.id)
    assert body["task"]["has_project_plan"] is False
    assert body["summary"]["total_steps"] == 4
    assert body["summary"]["runnable_steps"] == 1
    assert body["summary"]["unassigned_steps"] == 1
    assert body["summary"]["active_runs"] == 1
    assert body["summary"]["handoff_counts"] == {
        "no_downstream": 2,
        "ready_for_downstream": 1,
        "source_incomplete": 1,
    }
    assert body["summary"]["ready_handoffs"] == 1
    assert body["summary"]["blocked_handoffs"] == 0
    by_package = {step["work_package_id"]: step for step in body["steps"]}
    assert by_package["design"]["blocked_reasons"] == []
    assert by_package["design"]["handoff"]["status"] == "ready_for_downstream"
    assert by_package["design"]["handoff"]["requires_handoff"] is True
    assert by_package["design"]["handoff"]["downstream_step_ids"] == [str(runnable_step.id)]
    assert by_package["design"]["handoff"]["runnable_downstream_step_ids"] == [
        str(runnable_step.id)
    ]
    assert by_package["design"]["handoff"]["recommended_actions"] == ["schedule_downstream_steps"]
    assert by_package["build"]["runnable"] is True
    assert by_package["build"]["dependency_state"]["satisfied"] is True
    assert by_package["build"]["handoff"]["status"] == "source_incomplete"
    assert by_package["build"]["handoff"]["upstream_step_ids"] == [str(completed_step.id)]
    assert by_package["build"]["handoff"]["downstream_step_ids"] == [str(blocked_step.id)]
    assert by_package["build"]["handoff"]["blocked_downstream_step_ids"] == [str(blocked_step.id)]
    assert by_package["review"]["assignment_status"] == "inactive_agent"
    assert by_package["review"]["dependency_state"]["satisfied"] is False
    assert by_package["review"]["handoff"]["status"] == "no_downstream"
    assert by_package["review"]["handoff"]["upstream_step_ids"] == [str(runnable_step.id)]
    assert set(by_package["review"]["blocked_reasons"]) == {
        "assigned_agent_inactive",
        "dependency_incomplete",
        "active_run_exists",
        "scheduler:workspace_run_quota_exceeded",
    }
    assert by_package["review"]["scheduling"]["blocked_reason"] == "workspace_run_quota_exceeded"
    assert by_package["review"]["dependencies"]["token"] == "[redacted]"
    assert by_package["review"]["runs"][0]["error"]["api_key"] == "[redacted]"
    assert by_package["release"]["assignment_status"] == "unassigned"
    assert by_package["release"]["blocked_reasons"] == ["agent_unassigned", "dependency_missing"]
    assert by_package["release"]["handoff"]["status"] == "no_downstream"
    assert "hidden-token" not in str(body)
    assert "sk-run" not in str(body)
    assert "sk-hidden" not in str(body)
    assert raw_diagnostics is not None
    assert "hidden-token" not in str(raw_diagnostics)
    assert "sk-run" not in str(raw_diagnostics)
    assert foreign_response.status_code == 403
    assert missing_response.status_code == 403


def test_task_handoff_queue_lists_attention_items_and_preserves_scope() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other-handoff@example.com", slug="other-handoff"
    )
    active_agent = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    inactive_agent = AgentProfile(
        workspace_id=workspace.id,
        name="Reviewer",
        role="reviewer",
        status="inactive",
        model_settings={"api_key": "sk-reviewer"},
    )
    session.add_all([active_agent, inactive_agent])
    session.flush()
    team = AgentTeam(workspace_id=workspace.id, name="Delivery Team", team_type="software")
    foreign_team = AgentTeam(
        workspace_id=other_workspace.id, name="Foreign Team", team_type="software"
    )
    session.add_all([team, foreign_team])
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Build handoff queue",
        status="running",
        priority=7,
        domain_type="software",
    )
    foreign_task = Task(
        workspace_id=other_workspace.id,
        created_by_user_id=other_owner.id,
        agent_team_id=foreign_team.id,
        title="Foreign task",
        status="running",
        priority=10,
    )
    session.add_all([task, foreign_task])
    session.flush()
    design_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=active_agent.id,
        work_package_id="design",
        title="Design",
        status="completed",
        order_index=10,
        result_summary="Design complete",
    )
    api_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=active_agent.id,
        work_package_id="api",
        title="API",
        status="completed",
        order_index=20,
        result_summary="API complete",
    )
    final_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=active_agent.id,
        work_package_id="final",
        title="Final summary",
        status="completed",
        order_index=50,
        result_summary="Ready for review",
    )
    foreign_step = TaskStep(
        workspace_id=other_workspace.id,
        task_id=foreign_task.id,
        work_package_id="foreign",
        title="Foreign",
        status="completed",
    )
    session.add_all([design_step, api_step, final_step, foreign_step])
    session.flush()
    build_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=active_agent.id,
        work_package_id="build",
        title="Build",
        status="queued",
        order_index=30,
        dependencies={"after_step_ids": [str(design_step.id)]},
    )
    review_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=inactive_agent.id,
        work_package_id="review",
        title="Review",
        status="queued",
        order_index=40,
        dependencies={
            "after_step_ids": [str(api_step.id)],
            "blocked_reason": "worker_unavailable",
            "token": "hidden-token",
        },
    )
    session.add_all([build_step, review_step])
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/handoff-queue?team_id={team.id}",
        headers=_headers(owner.id),
    )
    ready_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/handoff-queue?handoff_status=ready_for_downstream",
        headers=_headers(owner.id),
    )
    include_terminal_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/handoff-queue?include_terminal=true",
        headers=_headers(owner.id),
    )
    foreign_team_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/handoff-queue?team_id={foreign_team.id}",
        headers=_headers(owner.id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["team_id"] == str(team.id)
    assert body["total"] == 3
    assert body["summary"]["handoff_status_counts"] == {
        "downstream_blocked": 1,
        "final_delivery_ready": 1,
        "ready_for_downstream": 1,
    }
    assert body["summary"]["recommended_actions"] == {
        "inspect_blocked_downstream": 1,
        "request_manager_review": 1,
        "schedule_downstream_steps": 1,
    }
    team_action_plan = {
        item["action"]: item for item in body["summary"]["team_operator_action_plan"]
    }
    assert team_action_plan["schedule_downstream_steps"] == {
        "team_id": str(team.id),
        "action": "schedule_downstream_steps",
        "automation": "team_operator_action",
        "api_route": "POST /api/v1/workspaces/{workspace_id}/teams/{team_id}/operator-actions",
        "task_ids": [str(task.id)],
        "task_step_ids": [str(design_step.id)],
        "count": 1,
        "reason": "handoff_queue",
        "payload_template": {
            "action": "schedule_downstream_steps",
            "task_ids": [str(task.id)],
            "task_step_ids": [str(design_step.id)],
            "reason": "handoff_queue",
            "metadata": {"source": "handoff_queue"},
        },
    }
    assert team_action_plan["request_manager_review"]["payload_template"] == {
        "action": "request_manager_review",
        "task_ids": [str(task.id)],
        "task_step_ids": [],
        "reason": "handoff_queue",
        "metadata": {"source": "handoff_queue"},
    }
    by_package = {item["work_package_id"]: item for item in body["items"]}
    assert by_package["design"]["handoff_status"] == "ready_for_downstream"
    assert by_package["design"]["runnable_downstream_step_ids"] == [str(build_step.id)]
    assert by_package["api"]["handoff_status"] == "downstream_blocked"
    assert by_package["api"]["blocked_downstream_step_ids"] == [str(review_step.id)]
    assert by_package["final"]["handoff_status"] == "final_delivery_ready"
    assert ready_response.status_code == 200
    assert ready_response.json()["total"] == 1
    assert include_terminal_response.status_code == 200
    assert include_terminal_response.json()["total"] == 5
    assert foreign_team_response.status_code == 404
    serialized = str(body)
    assert "hidden-token" not in serialized
    assert "sk-reviewer" not in serialized
    assert "Foreign task" not in serialized


def test_task_manager_diagnostics_explains_acceptance_follow_up_and_redacts() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-manager-diagnostics@example.com",
        slug="other-manager-diagnostics",
    )
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    developer = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    session.add_all([manager, developer])
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Manager diagnostics",
        status="running",
        team_snapshot={"team": {"manager_agent_profile_id": str(manager.id)}},
        project_plan={"planner_agent_profile_id": str(manager.id)},
    )
    other_task = Task(
        workspace_id=other_workspace.id,
        created_by_user_id=other_owner.id,
        title="Foreign manager diagnostics",
    )
    session.add_all([task, other_task])
    session.flush()
    planning_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=manager.id,
        work_package_id="manager-planning",
        review_policy={"mode": "agent_planning"},
        required_role="project_manager",
        title="Plan work",
        status="completed",
        order_index=10,
    )
    session.add(planning_step)
    session.flush()
    build_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=developer.id,
        work_package_id="build",
        required_role="developer",
        title="Build feature",
        status="completed",
        order_index=20,
        dependencies={"after_step_ids": [str(planning_step.id)]},
    )
    session.add(build_step)
    session.flush()
    summary_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=manager.id,
        work_package_id="manager-summary",
        review_policy={"mode": "final_acceptance"},
        required_role="project_manager",
        title="Review delivery",
        status="completed",
        order_index=30,
        dependencies={"after_step_ids": [str(build_step.id)]},
    )
    session.add(summary_step)
    session.flush()
    revision_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=developer.id,
        work_package_id="revision-build-1-1",
        required_role="developer",
        title="Revise feature",
        status="queued",
        order_index=40,
        dependencies={"revision_of_work_package_id": "build", "token": "hidden-token"},
    )
    session.add(revision_step)
    session.flush()
    review_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=manager.id,
        work_package_id="manager-summary-revision-1",
        review_policy={"mode": "revision_review"},
        required_role="project_manager",
        title="Review revision",
        status="queued",
        order_index=50,
        dependencies={"after_step_ids": [str(revision_step.id)]},
    )
    session.add(review_step)
    session.flush()
    session.add_all(
        [
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=summary_step.id,
                agent_profile_id=manager.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body="Private acceptance body should not be returned.",
                payload={
                    "decision": "request_revision",
                    "summary": "Needs one revision",
                    "reasons": ["Missing tests"],
                    "revision_requests": [
                        {
                            "work_package_id": "build",
                            "instruction": "Add tests",
                            "token": "hidden-token",
                        }
                    ],
                    "api_key": "sk-message",
                },
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=summary_step.id,
                agent_profile_id=manager.id,
                message_type="pm.follow_up_created",
                sequence=2,
                body="Follow up created.",
                payload={
                    "decision": "request_revision",
                    "revision_cycle": 1,
                    "follow_up_step_ids": [str(revision_step.id)],
                    "follow_up_work_package_ids": ["revision-build-1-1"],
                    "headers": {"authorization": "Bearer hidden"},
                },
            ),
            TaskMessage(
                workspace_id=other_workspace.id,
                task_id=other_task.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body="foreign",
                payload={"decision": "approved"},
            ),
        ]
    )
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/manager-diagnostics",
        headers=_headers(owner.id),
    )
    foreign_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/tasks/{task.id}/manager-diagnostics",
        headers=_headers(other_owner.id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["manager"]["agent"]["name"] == "PM"
    assert body["manager"]["planning_step_id"] == str(planning_step.id)
    assert body["summary"]["status"] == "attention"
    assert body["summary"]["decision_counts"] == {"request_revision": 1}
    assert set(body["blocked_reasons"]) == {"specialist_steps_incomplete", "follow_up_incomplete"}
    chain = {item["phase"]: item for item in body["handoff_chain"]}
    assert chain["manager_planning"]["status"] == "completed"
    assert chain["specialist_execution"]["status"] == "in_progress"
    assert chain["manager_acceptance"]["status"] == "completed"
    assert chain["follow_up"]["blocked_reasons"] == ["follow_up_incomplete"]
    decision = body["acceptance_decisions"][0]
    assert decision["decision"] == "request_revision"
    assert decision["status"] == "follow_up_created"
    assert decision["summary"] == "Needs one revision"
    assert decision["revision_requests"][0]["token"] == "[redacted]"
    assert decision["metadata"]["api_key"] == "[redacted]"
    cycle = body["follow_up_cycles"][0]
    assert cycle["revision_cycle"] == 1
    assert cycle["status"] == "in_progress"
    assert cycle["follow_up_step_ids"] == [str(revision_step.id)]
    assert set(cycle["blocked_reasons"]) == {
        "follow_up_steps_incomplete",
        "follow_up_review_incomplete",
    }
    assert cycle["metadata"]["headers"] == "[redacted]"
    assert foreign_response.status_code == 403
    serialized = str(body)
    assert "Private acceptance body should not be returned." not in serialized
    assert "hidden-token" not in serialized
    assert "sk-message" not in serialized
    assert "Bearer hidden" not in serialized
    assert "foreign" not in serialized


def test_task_manager_queue_lists_attention_items_and_preserves_workspace_scope() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other-manager-queue@example.com", slug="other-manager-queue"
    )
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
        model_settings={"api_key": "sk-manager"},
    )
    developer = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    session.add_all([manager, developer])
    session.flush()
    delivery_team = AgentTeam(
        workspace_id=workspace.id,
        name="Delivery Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    support_team = AgentTeam(
        workspace_id=workspace.id,
        name="Support Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    foreign_team = AgentTeam(
        workspace_id=other_workspace.id, name="Foreign Team", team_type="software"
    )
    session.add_all([delivery_team, support_team, foreign_team])
    session.flush()
    needs_follow_up = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=delivery_team.id,
        title="Needs manager follow up",
        status="running",
        priority=8,
        domain_type="software",
        team_snapshot={"team": {"manager_agent_profile_id": str(manager.id)}},
        project_plan={"planner_agent_profile_id": str(manager.id)},
    )
    healthy = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=support_team.id,
        title="Approved delivery",
        status="completed",
        priority=1,
        domain_type="software",
        team_snapshot={"team": {"manager_agent_profile_id": str(manager.id)}},
        project_plan={"planner_agent_profile_id": str(manager.id)},
    )
    foreign_task = Task(
        workspace_id=other_workspace.id,
        created_by_user_id=other_owner.id,
        agent_team_id=foreign_team.id,
        title="Foreign manager queue task",
        status="running",
    )
    session.add_all([needs_follow_up, healthy, foreign_task])
    session.flush()
    follow_up_steps = [
        TaskStep(
            workspace_id=workspace.id,
            task_id=needs_follow_up.id,
            assigned_agent_profile_id=manager.id,
            work_package_id="manager-planning",
            review_policy={"mode": "agent_planning"},
            required_role="project_manager",
            title="Plan attention task",
            status="completed",
            order_index=10,
        ),
        TaskStep(
            workspace_id=workspace.id,
            task_id=needs_follow_up.id,
            assigned_agent_profile_id=developer.id,
            work_package_id="build",
            required_role="developer",
            title="Build attention task",
            status="completed",
            order_index=20,
        ),
        TaskStep(
            workspace_id=workspace.id,
            task_id=needs_follow_up.id,
            assigned_agent_profile_id=manager.id,
            work_package_id="manager-summary",
            review_policy={"mode": "final_acceptance"},
            required_role="project_manager",
            title="Review attention task",
            status="completed",
            order_index=30,
        ),
    ]
    healthy_steps = [
        TaskStep(
            workspace_id=workspace.id,
            task_id=healthy.id,
            assigned_agent_profile_id=manager.id,
            work_package_id="manager-planning",
            review_policy={"mode": "agent_planning"},
            required_role="project_manager",
            title="Plan healthy task",
            status="completed",
            order_index=10,
        ),
        TaskStep(
            workspace_id=workspace.id,
            task_id=healthy.id,
            assigned_agent_profile_id=developer.id,
            work_package_id="build",
            required_role="developer",
            title="Build healthy task",
            status="completed",
            order_index=20,
        ),
        TaskStep(
            workspace_id=workspace.id,
            task_id=healthy.id,
            assigned_agent_profile_id=manager.id,
            work_package_id="manager-summary",
            review_policy={"mode": "final_acceptance"},
            required_role="project_manager",
            title="Review healthy task",
            status="completed",
            order_index=30,
        ),
    ]
    session.add_all([*follow_up_steps, *healthy_steps])
    session.flush()
    session.add_all(
        [
            TaskMessage(
                workspace_id=workspace.id,
                task_id=needs_follow_up.id,
                task_step_id=follow_up_steps[2].id,
                agent_profile_id=manager.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body="Private body should not be serialized.",
                payload={
                    "decision": "request_revision",
                    "summary": "Needs tests",
                    "revision_requests": [{"work_package_id": "build", "instruction": "Add tests"}],
                    "token": "hidden-token",
                },
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=healthy.id,
                task_step_id=healthy_steps[2].id,
                agent_profile_id=manager.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body="Approved body should not be serialized.",
                payload={"decision": "approved", "summary": "Approved", "api_key": "sk-approved"},
            ),
            TaskMessage(
                workspace_id=other_workspace.id,
                task_id=foreign_task.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body="foreign",
                payload={"decision": "request_revision"},
            ),
        ]
    )
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/manager-queue", headers=_headers(owner.id)
    )
    include_healthy = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/manager-queue?include_healthy=true",
        headers=_headers(owner.id),
    )
    status_filtered = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/manager-queue?include_healthy=true&status=completed",
        headers=_headers(owner.id),
    )
    delivery_filtered = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/manager-queue?include_healthy=true&team_id={delivery_team.id}",
        headers=_headers(owner.id),
    )
    support_filtered = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/manager-queue?include_healthy=true&team_id={support_team.id}",
        headers=_headers(owner.id),
    )
    foreign_team_filtered = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/manager-queue?include_healthy=true&team_id={foreign_team.id}",
        headers=_headers(owner.id),
    )
    foreign_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/tasks/manager-queue?include_healthy=true",
        headers=_headers(other_owner.id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["summary"]["needs_attention"] == 1
    assert body["summary"]["pending_phases"] == {"follow_up": 1}
    assert body["summary"]["team_operator_action_plan"] == [
        {
            "team_id": str(delivery_team.id),
            "action": "request_manager_review",
            "automation": "team_operator_action",
            "api_route": "POST /api/v1/workspaces/{workspace_id}/teams/{team_id}/operator-actions",
            "task_ids": [str(needs_follow_up.id)],
            "task_step_ids": [],
            "count": 1,
            "reason": "manager_queue",
            "payload_template": {
                "action": "request_manager_review",
                "task_ids": [str(needs_follow_up.id)],
                "task_step_ids": [],
                "reason": "manager_queue",
                "metadata": {"source": "manager_queue"},
            },
        }
    ]
    item = body["items"][0]
    assert item["task_id"] == str(needs_follow_up.id)
    assert item["team_id"] == str(delivery_team.id)
    assert item["manager_agent_profile_id"] == str(manager.id)
    assert item["manager_agent_name"] == "PM"
    assert item["manager_status"] == "active"
    assert item["summary_status"] == "blocked"
    assert item["pending_phase"] == "follow_up"
    assert item["needs_attention"] is True
    assert item["blocked_reasons"] == ["follow_up_missing"]
    assert item["recommended_actions"] == ["request_manager_review"]
    assert item["acceptance_decisions"] == 1
    assert item["follow_up_cycles"] == 0
    assert item["step_status_counts"] == {"completed": 3}
    assert include_healthy.status_code == 200
    assert include_healthy.json()["total"] == 2
    by_title = {item["title"]: item for item in include_healthy.json()["items"]}
    assert by_title["Approved delivery"]["needs_attention"] is False
    assert by_title["Approved delivery"]["pending_phase"] == "none"
    assert status_filtered.status_code == 200
    assert status_filtered.json()["items"][0]["task_id"] == str(healthy.id)
    assert delivery_filtered.status_code == 200
    assert delivery_filtered.json()["team_id"] == str(delivery_team.id)
    assert delivery_filtered.json()["total"] == 1
    assert delivery_filtered.json()["items"][0]["task_id"] == str(needs_follow_up.id)
    assert support_filtered.status_code == 200
    assert support_filtered.json()["team_id"] == str(support_team.id)
    assert support_filtered.json()["total"] == 1
    assert support_filtered.json()["items"][0]["task_id"] == str(healthy.id)
    assert foreign_team_filtered.status_code == 404
    assert foreign_response.status_code == 200
    assert foreign_response.json()["total"] == 1
    assert foreign_response.json()["items"][0]["title"] == "Foreign manager queue task"
    serialized = str(include_healthy.json())
    assert "Private body should not be serialized." not in serialized
    assert "Approved body should not be serialized." not in serialized
    assert "hidden-token" not in serialized
    assert "sk-approved" not in serialized
    assert "sk-manager" not in serialized
    assert "foreign" not in str(body)


def test_task_observation_composes_domain_sections_and_sanitizes_payloads() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other@example.com", slug="other-space"
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        domain_type="novel",
        title="Write a mystery novel",
        status="running",
        priority=7,
        input={"outline": ["Act 1", "Act 2"], "api_key": "sk-observation-input"},
        generic_state={"word_count": 3200, "headers": {"authorization": "Bearer generic"}},
        domain_state={
            "outline": ["Act 1", "Act 2"],
            "chapters": [{"title": "Chapter 1", "status": "drafting"}],
            "characters": [{"name": "Lin", "role": "detective"}],
            "word_count": 3200,
            "token": "domain-hidden-token",
        },
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Draft chapter 1",
        status="running",
        order_index=1,
        work_package_id="chapter-1",
        required_role="writer",
        required_skills=["plotting"],
    )
    correction_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Revise chapter 1",
        status="queued",
        order_index=2,
        work_package_id="correction-revise-2",
        dependencies={
            "correction": {
                "mode": "revise",
                "target": {"target_type": "step", "task_step_id": str(step.id)},
                "instruction": "Make the opening more suspenseful.",
                "metadata": {"risk_level": "medium"},
            },
            "blocked_reason": "workspace_quota_exceeded:active_runs",
            "blocked_resource_keys": ["active_runs"],
        },
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        status=RunStatus.RUNNING.value,
        input={},
    )
    session.add_all([step, correction_step, run])
    session.flush()
    session.add_all(
        [
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=step.id,
                agent_run_id=run.id,
                message_type="step.started",
                sequence=1,
                body="Started drafting.",
                payload={"note": "draft", "api_key": "sk-hidden"},
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                message_type="pm.acceptance_decision",
                sequence=2,
                body="Keep writing.",
                payload={
                    "decision": "request_revision",
                    "summary": "Opening is too flat.",
                    "reasons": ["Weak hook"],
                    "revision_requests": [
                        {"work_package_id": "chapter-1", "instruction": "Raise tension."}
                    ],
                    "risk_level": "medium",
                    "token": "hidden-token",
                },
            ),
            RunEvent(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                event_type="run.started",
                sequence=1,
                message="Run started",
                event_metadata={},
                created_at=datetime.now(UTC),
            ),
            Artifact(
                workspace_id=workspace.id,
                task_id=task.id,
                agent_run_id=run.id,
                artifact_type="draft",
                filename="chapter-1.md",
                content_type="text/markdown",
                size_bytes=1024,
                checksum_sha256="a" * 64,
                storage_key=f"workspaces/{workspace.id}/artifacts/{uuid4()}",
                artifact_metadata={"work_package_id": "chapter-1"},
                created_at=datetime.now(UTC),
            ),
        ]
    )
    session.commit()
    observed = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/observation", headers=_headers(owner.id)
    )
    forced = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/observation?view_type=software",
        headers=_headers(owner.id),
    )
    unsupported = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/observation?view_type=finance",
        headers=_headers(owner.id),
    )
    foreign = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/tasks/{task.id}/observation",
        headers=_headers(other_owner.id),
    )
    raw_observation = TaskObservationService(session).get_observation(
        workspace_id=workspace.id, task_id=task.id
    )
    assert observed.status_code == 200
    body = observed.json()
    assert body["view_type"] == "novel"
    assert body["summary"]["status"] == "running"
    assert body["summary"]["progress"] == 0.0
    assert body["summary"]["artifact_count"] == 1
    sections = {section["key"]: section for section in body["sections"]}
    assert set(sections) == {"overview", "timeline", "artifacts", "review", "quality", "domain"}
    domain_cards = sections["domain"]["cards"]
    assert domain_cards[0]["card_type"] == "manuscript_status"
    assert domain_cards[0]["status"] == "attention"
    assert domain_cards[0]["data"]["outline_item_count"] == 2
    assert domain_cards[0]["data"]["chapter_count"] == 1
    assert domain_cards[0]["data"]["character_count"] == 1
    assert domain_cards[0]["data"]["word_count"] == 3200
    assert domain_cards[0]["data"]["recommended_actions"] == [
        "check_continuity",
        "request_editorial_review",
    ]
    assert domain_cards[1]["card_type"] == "outline"
    assert domain_cards[2]["data"]["value"] == [{"title": "Chapter 1", "status": "drafting"}]
    message_payload = sections["timeline"]["cards"][0]["data"]["payload"]
    review_payload = sections["review"]["cards"][0]["data"]["payload"]
    assert message_payload == {"note": "draft", "api_key": "[redacted]"}
    assert review_payload["token"] == "[redacted]"
    assert review_payload["decision"] == "request_revision"
    quality_cards = sections["quality"]["cards"]
    assert any(
        card["card_type"] == "revision_history"
        and card["data"]["instruction"] == "Make the opening more suspenseful."
        for card in quality_cards
    )
    assert any(
        card["card_type"] == "revision_history"
        and card["data"]["source"] == "pm_acceptance"
        and (card["data"]["revision_requests"][0]["instruction"] == "Raise tension.")
        for card in quality_cards
    )
    assert any(
        card["card_type"] == "risk_flag"
        and card["data"]["source"] == "scheduler"
        and (card["data"]["reason"] == "workspace_quota_exceeded:active_runs")
        for card in quality_cards
    )
    assert any(
        card["card_type"] == "risk_flag"
        and card["data"]["source"] == "pm.acceptance_decision"
        and (card["data"]["risk"]["severity"] == "medium")
        for card in quality_cards
    )
    assert sections["artifacts"]["cards"][0]["title"] == "chapter-1.md"
    assert forced.status_code == 200
    forced_body = forced.json()
    assert forced_body["view_type"] == "software"
    forced_domain = {section["key"]: section for section in forced_body["sections"]}["domain"]
    assert forced_domain["cards"][0]["card_type"] == "delivery_status"
    assert "capture_requirements" in forced_domain["cards"][0]["data"]["recommended_actions"]
    assert unsupported.status_code == 400
    assert foreign.status_code == 403
    serialized = str(body)
    assert "sk-observation-input" not in serialized
    assert "Bearer generic" not in serialized
    assert "domain-hidden-token" not in serialized
    assert raw_observation is not None
    assert "sk-hidden" not in str(raw_observation)
    assert "hidden-token" not in str(raw_observation)


def test_task_observation_status_cards_for_specialized_domains() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    aigc_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        domain_type="aigc",
        title="Create launch visuals",
        input={"prompt": "clean product render"},
        generic_state={"model_settings": {"size": "1024x1024"}},
        domain_state={
            "prompt": "clean product render",
            "model_settings": {"size": "1024x1024"},
            "variants": [{"id": "v1"}, {"id": "v2"}],
            "selected_asset": {"id": "v2", "status": "approved"},
            "review_notes": ["Approved for launch"],
        },
    )
    research_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        domain_type="research",
        title="Research competitors",
        domain_state={
            "sources": [{"url": "https://example.test/a"}],
            "claims": [{"text": "Competitor A is faster"}],
            "citations": [{"source": "source-a"}],
            "report_sections": [{"title": "Summary"}],
            "confidence": "medium",
        },
    )
    software_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        domain_type="software",
        title="Build worker dashboard API",
        domain_state={
            "requirements": ["show worker backlog"],
            "design_tasks": ["add endpoint contract"],
            "branches": ["feature/workers"],
            "patches": ["worker-dashboard.patch"],
            "tests": ["test_worker_dashboard"],
            "build_status": "passed",
            "review_comments": [],
        },
    )
    session.add_all([aigc_task, research_task, software_task])
    session.flush()
    session.add_all(
        [
            Artifact(
                workspace_id=workspace.id,
                task_id=aigc_task.id,
                artifact_type="image",
                filename="launch.png",
                content_type="image/png",
                size_bytes=128,
                checksum_sha256="a" * 64,
                storage_key="aigc-launch",
                created_at=datetime.now(UTC),
            ),
            Artifact(
                workspace_id=workspace.id,
                task_id=research_task.id,
                artifact_type="report",
                filename="research.md",
                content_type="text/markdown",
                size_bytes=128,
                checksum_sha256="b" * 64,
                storage_key="research-report",
                created_at=datetime.now(UTC),
            ),
        ]
    )
    session.commit()
    aigc = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{aigc_task.id}/observation",
        headers=_headers(owner.id),
    )
    research = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{research_task.id}/observation",
        headers=_headers(owner.id),
    )
    software = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{software_task.id}/observation",
        headers=_headers(owner.id),
    )
    assert aigc.status_code == 200
    aigc_domain = {section["key"]: section for section in aigc.json()["sections"]}["domain"]
    assert aigc_domain["cards"][0]["card_type"] == "production_status"
    assert aigc_domain["cards"][0]["status"] == "healthy"
    assert aigc_domain["cards"][0]["data"]["variant_count"] == 2
    assert aigc_domain["cards"][0]["data"]["recommended_actions"] == []
    assert research.status_code == 200
    research_domain = {section["key"]: section for section in research.json()["sections"]}["domain"]
    assert research_domain["cards"][0]["card_type"] == "research_status"
    assert research_domain["cards"][0]["data"]["source_count"] == 1
    assert research_domain["cards"][0]["data"]["recommended_actions"] == []
    assert software.status_code == 200
    software_domain = {section["key"]: section for section in software.json()["sections"]}["domain"]
    assert software_domain["cards"][0]["card_type"] == "delivery_status"
    assert software_domain["cards"][0]["status"] == "healthy"
    assert software_domain["cards"][0]["data"]["build_status"] == "passed"


def test_task_correction_rejects_foreign_artifact_target() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    _, other_workspace = _seed_workspace(
        session, role="owner", email="other-correction@example.com", slug="other-correction"
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Replace artifact",
        status="running",
    )
    foreign_task = Task(workspace_id=other_workspace.id, title="Foreign", status="running")
    session.add_all([task, foreign_task])
    session.flush()
    artifact = Artifact(
        workspace_id=other_workspace.id,
        task_id=foreign_task.id,
        artifact_type="document",
        filename="foreign.pdf",
        content_type="application/pdf",
        size_bytes=10,
        checksum_sha256="a" * 64,
        storage_key="foreign",
        created_at=datetime.now(UTC),
    )
    session.add(artifact)
    session.commit()
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/corrections",
        headers=_headers(owner.id),
        json={
            "target_type": "artifact",
            "target_id": str(artifact.id),
            "mode": "replace_artifact",
            "instruction": "Replace this file.",
        },
    )
    assert response.status_code == 400
    assert "does not belong" in response.json()["error"]["message"]


def test_artifact_correction_creates_replacement_work_without_mutating_artifact() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Replace owned artifact",
        status="running",
    )
    session.add(task)
    session.flush()
    artifact = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        artifact_type="document",
        filename="draft.pdf",
        content_type="application/pdf",
        size_bytes=10,
        checksum_sha256="b" * 64,
        storage_key="owned",
        artifact_metadata={"version": 1},
        created_at=datetime.now(UTC),
    )
    session.add(artifact)
    session.commit()
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/corrections",
        headers=_headers(owner.id),
        json={
            "target_type": "artifact",
            "target_id": str(artifact.id),
            "mode": "replace_artifact",
            "instruction": "Replace the PDF with the approved version.",
        },
    )
    assert response.status_code == 201
    session.refresh(artifact)
    step = session.get(TaskStep, UUID(response.json()["created_step_id"]))
    assert artifact.artifact_metadata == {"version": 1}
    assert step is not None
    assert step.expected_artifacts == ["replacement_artifact"]
    assert step.dependencies["correction"]["target"]["artifact_id"] == str(artifact.id)


def test_task_correction_diagnostics_tracks_follow_up_status_and_redacts_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", with_provider=True)
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-correction-diagnostics@example.com",
        slug="other-correction-diagnostics",
        with_provider=True,
    )
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, owner.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
        created_by_user_id=owner.id,
        title="Correction diagnostics",
        status="running",
    )
    session.add(task)
    session.flush()
    draft_step = TaskStep(
        assigned_agent_profile_id=task.owner_agent_profile_id,
        workspace_id=workspace.id,
        task_id=task.id,
        title="Draft",
        status="completed",
        order_index=1,
    )
    session.add(draft_step)
    session.flush()
    artifact = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=draft_step.id,
        artifact_type="document",
        filename="draft.pdf",
        content_type="application/pdf",
        size_bytes=10,
        checksum_sha256="a" * 64,
        storage_key="draft",
        created_at=datetime.now(UTC),
    )
    session.add(artifact)
    session.commit()
    replace = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/corrections",
        headers=_headers(owner.id),
        json={
            "target_type": "artifact",
            "target_id": str(artifact.id),
            "mode": "replace_artifact",
            "instruction": "Replace the draft PDF.",
            "metadata": {"token": "hidden-token"},
        },
    )
    revise = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/corrections",
        headers=_headers(owner.id),
        json={
            "target_type": "step",
            "target_id": str(draft_step.id),
            "mode": "revise",
            "instruction": "Revise the draft text.",
        },
    )
    replace_step = session.get(TaskStep, UUID(replace.json()["created_step_id"]))
    assert replace_step is not None
    replace_step.status = "completed"
    replace_step.result_summary = "Replacement complete"
    session.add(
        AgentRun(
            workspace_id=workspace.id,
            task_id=task.id,
            task_step_id=replace_step.id,
            status=RunStatus.COMPLETED.value,
            input={},
            error={"api_key": "sk-hidden"},
        )
    )
    replacement_artifact = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=replace_step.id,
        artifact_type="document",
        filename="replacement.pdf",
        content_type="application/pdf",
        size_bytes=12,
        checksum_sha256="b" * 64,
        storage_key="replacement",
        created_at=datetime.now(UTC),
    )
    session.add(replacement_artifact)
    session.commit()
    stop = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/corrections",
        headers=_headers(owner.id),
        json={"target_type": "task", "mode": "stop_work", "instruction": "Stop remaining work."},
    )
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/corrections/diagnostics",
        headers=_headers(owner.id),
    )
    foreign_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/tasks/{task.id}/corrections/diagnostics",
        headers=_headers(other_owner.id),
    )
    raw_diagnostics = TaskCorrectionDiagnosticsService(session).get_diagnostics(
        workspace_id=workspace.id, task_id=task.id
    )
    assert replace.status_code == 201
    assert revise.status_code == 201
    assert stop.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_corrections"] == 3
    assert body["summary"]["status_counts"] == {"cancelled": 1, "completed": 1, "in_progress": 1}
    assert body["summary"]["mode_counts"] == {"replace_artifact": 1, "revise": 1, "stop_work": 1}
    assert body["summary"]["blocked_corrections"] == 0
    by_mode = {item["mode"]: item for item in body["corrections"]}
    assert by_mode["replace_artifact"]["status"] == "completed"
    assert by_mode["replace_artifact"]["metadata"]["token"] == "[redacted]"
    assert by_mode["replace_artifact"]["created_step"]["result_summary"] == "Replacement complete"
    assert by_mode["replace_artifact"]["artifacts"][0]["filename"] == "replacement.pdf"
    assert by_mode["replace_artifact"]["blocked_reasons"] == []
    assert by_mode["revise"]["status"] == "in_progress"
    assert by_mode["revise"]["blocked_reasons"] == []
    assert by_mode["stop_work"]["status"] == "cancelled"
    assert "hidden-token" not in str(body)
    assert "sk-hidden" not in str(body)
    assert raw_diagnostics is not None
    assert "hidden-token" not in str(raw_diagnostics)
    assert "sk-hidden" not in str(raw_diagnostics)
    assert foreign_response.status_code == 403


def test_task_timeline_returns_execution_events_and_redacts_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other-timeline@example.com", slug="other-timeline"
    )
    agent = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Build timeline",
        status="running",
        priority=9,
        domain_type="software",
        input={"api_key": "sk-task"},
        created_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
    )
    session.add_all([agent, task])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        work_package_id="build",
        required_role="developer",
        required_skills=["python"],
        expected_artifacts=["patch"],
        acceptance_criteria=["tests pass"],
        review_policy={"headers": {"authorization": "Bearer hidden"}},
        title="Build API",
        description="Implement the API",
        status="running",
        order_index=10,
        dependencies={"token": "hidden-token"},
        created_at=datetime(2026, 1, 1, 9, 5, tzinfo=UTC),
    )
    session.add(step)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        agent_profile_id=agent.id,
        status=RunStatus.COMPLETED.value,
        input={
            "prompt": "build",
            "base_url": "https://router.example.test/private",
            "headers": {"authorization": "Bearer hidden"},
            "authorization_snapshot": {
                "allowed_tools": ["write_artifact", "read_workspace_file"],
                "model_provider": {
                    "provider": "openai-compatible",
                    "selected_model": "gpt-test",
                    "model_api": "responses",
                    "api_key": "sk-provider-hidden",
                    "base_url": "https://provider.example.test/private",
                },
            },
        },
        output={"result": "ok", "token": "hidden-token"},
        error={"api_key": "sk-run"},
        model="gpt-test",
        started_at=datetime(2026, 1, 1, 9, 10, tzinfo=UTC),
        completed_at=datetime(2026, 1, 1, 9, 20, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, 9, 6, tzinfo=UTC),
    )
    session.add(run)
    session.flush()
    session.add_all(
        [
            RunEvent(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                event_type="tool.completed",
                sequence=1,
                message="Tool completed",
                event_metadata={"secret": "tool-secret", "safe": "ok"},
                created_at=datetime(2026, 1, 1, 9, 15, tzinfo=UTC),
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=step.id,
                agent_run_id=run.id,
                agent_profile_id=agent.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body="Do not expose this full message body.",
                payload={
                    "summary": "Needs review",
                    "api_key": "sk-message",
                    "nested": {"authorization": "Bearer hidden"},
                },
                created_at=datetime(2026, 1, 1, 9, 25, tzinfo=UTC),
            ),
            TaskMessage(
                workspace_id=other_workspace.id,
                task_id=task.id,
                message_type="foreign.message",
                sequence=2,
                body="foreign",
                payload={},
                created_at=datetime(2026, 1, 1, 9, 30, tzinfo=UTC),
            ),
            Artifact(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=step.id,
                agent_run_id=run.id,
                agent_profile_id=agent.id,
                work_package_id="build",
                version=1,
                review_status="approved",
                artifact_type="patch",
                filename="patch.diff",
                content_type="text/x-diff",
                size_bytes=128,
                checksum_sha256="b" * 64,
                storage_key="secret-storage-key",
                artifact_metadata={"token": "artifact-token", "safe": "ok"},
                created_at=datetime(2026, 1, 1, 9, 30, tzinfo=UTC),
            ),
        ]
    )
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/timeline", headers=_headers(owner.id)
    )
    limited = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/timeline?limit=3",
        headers=_headers(owner.id),
    )
    foreign_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/tasks/{task.id}/timeline",
        headers=_headers(other_owner.id),
    )
    raw_timeline = TaskTimelineService(session).get_timeline(
        workspace_id=workspace.id, task_id=task.id
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["summary"]["total_events"] == 8
    assert body["summary"]["source_counts"] == {
        "artifact": 1,
        "message": 1,
        "run": 3,
        "run_event": 1,
        "step": 1,
        "task": 1,
    }
    event_types = [event["event_type"] for event in body["events"]]
    assert event_types == [
        "task.created",
        "step.created",
        "run.created",
        "run.started",
        "tool.completed",
        "run.completed",
        "pm.acceptance_decision",
        "artifact.created",
    ]
    by_type = {event["event_type"]: event for event in body["events"]}
    assert by_type["step.created"]["agent"]["name"] == "Developer"
    assert by_type["step.created"]["metadata"]["review_policy"]["headers"] == "[redacted]"
    assert by_type["step.created"]["metadata"]["dependencies"]["token"] == "[redacted]"
    assert by_type["run.created"]["metadata"]["input_keys"] == [
        "authorization_snapshot",
        "base_url",
        "headers",
        "prompt",
    ]
    assert "input" not in by_type["run.created"]["metadata"]
    assert by_type["run.created"]["metadata"]["run_scope_snapshot"] == {
        "allowed_tools": ["write_artifact", "read_workspace_file"],
        "model_provider": {
            "provider": "openai-compatible",
            "selected_model": "gpt-test",
            "model_api": "responses",
        },
    }
    assert by_type["run.created"]["metadata"]["error"]["api_key"] == "[redacted]"
    assert by_type["tool.completed"]["metadata"]["secret"] == "[redacted]"
    assert by_type["pm.acceptance_decision"]["summary"] == "Needs review"
    assert by_type["pm.acceptance_decision"]["metadata"]["api_key"] == "[redacted]"
    assert by_type["artifact.created"]["metadata"]["artifact_metadata"]["token"] == "[redacted]"
    assert limited.status_code == 200
    assert limited.json()["summary"]["returned_events"] == 3
    assert limited.json()["summary"]["truncated_count"] == 5
    assert foreign_response.status_code == 403
    serialized = str(body)
    assert "sk-task" not in serialized
    assert "router.example.test/private" not in serialized
    assert "provider.example.test/private" not in serialized
    assert "sk-provider-hidden" not in serialized
    assert "hidden-token" not in serialized
    assert "tool-secret" not in serialized
    assert "sk-message" not in serialized
    assert "artifact-token" not in serialized
    assert "secret-storage-key" not in serialized
    assert "Do not expose this full message body." not in serialized
    assert "foreign" not in serialized
    assert raw_timeline is not None
    assert "Bearer hidden" not in str(raw_timeline)
    assert "hidden-token" not in str(raw_timeline)
    assert "tool-secret" not in str(raw_timeline)


def test_artifact_list_includes_work_package_version_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = Task(workspace_id=workspace.id, created_by_user_id=owner.id, title="Artifact metadata")
    session.add(task)
    session.flush()
    artifact = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="research-1",
        version=3,
        review_status="approved",
        artifact_type="document",
        filename="report.pdf",
        content_type="application/pdf",
        size_bytes=10,
        checksum_sha256="c" * 64,
        storage_key="owned",
        created_at=datetime.now(UTC),
    )
    session.add(artifact)
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/artifacts", headers=_headers(owner.id)
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["work_package_id"] == "research-1"
    assert item["version"] == 3
    assert item["review_status"] == "approved"
    assert item["task_step_id"] is None
    assert item["agent_profile_id"] is None
    assert item["supersedes_artifact_id"] is None
    assert "storage_key" not in item
    assert item["has_storage_object"] is True


def test_artifact_history_lists_versions_for_work_package_only() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other-artifacts@example.com", slug="other-artifacts"
    )
    task = Task(workspace_id=workspace.id, created_by_user_id=owner.id, title="Artifact history")
    other_task = Task(
        workspace_id=other_workspace.id,
        created_by_user_id=other_owner.id,
        title="Other artifact history",
    )
    session.add_all([task, other_task])
    session.flush()
    first = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="research-1",
        version=1,
        review_status="superseded",
        artifact_type="document",
        filename="report-v1.pdf",
        content_type="application/pdf",
        size_bytes=10,
        checksum_sha256="d" * 64,
        storage_key="owned-v1",
        created_at=datetime.now(UTC),
    )
    session.add(first)
    session.flush()
    second = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="research-1",
        version=2,
        supersedes_artifact_id=first.id,
        review_status="approved",
        artifact_type="document",
        filename="report-v2.pdf",
        content_type="application/pdf",
        size_bytes=12,
        checksum_sha256="e" * 64,
        storage_key="owned-v2",
        created_at=datetime.now(UTC),
    )
    other_package = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="other-package",
        version=1,
        artifact_type="document",
        filename="other.pdf",
        content_type="application/pdf",
        size_bytes=12,
        checksum_sha256="f" * 64,
        storage_key="other-package",
        created_at=datetime.now(UTC),
    )
    foreign = Artifact(
        workspace_id=other_workspace.id,
        task_id=other_task.id,
        work_package_id="research-1",
        version=99,
        artifact_type="document",
        filename="foreign.pdf",
        content_type="application/pdf",
        size_bytes=12,
        checksum_sha256="0" * 64,
        storage_key="foreign",
        created_at=datetime.now(UTC),
    )
    mismatched_task = Artifact(
        workspace_id=other_workspace.id,
        task_id=task.id,
        work_package_id="research-1",
        version=100,
        artifact_type="document",
        filename="mismatched-task.pdf",
        content_type="application/pdf",
        size_bytes=12,
        checksum_sha256="1" * 64,
        storage_key="mismatched-task",
        created_at=datetime.now(UTC),
    )
    session.add_all([second, other_package, foreign, mismatched_task])
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/artifacts/history?task_id={task.id}&work_package_id=research-1",
        headers=_headers(owner.id),
    )
    foreign_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/artifacts/history?task_id={task.id}&work_package_id=research-1",
        headers=_headers(other_owner.id),
    )
    foreign_owned_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/artifacts/history?task_id={other_task.id}&work_package_id=research-1",
        headers=_headers(other_owner.id),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["latest_artifact_id"] == str(second.id)
    assert payload["latest_version"] == 2
    assert [item["version"] for item in payload["items"]] == [2, 1]
    assert [item["filename"] for item in payload["items"]] == ["report-v2.pdf", "report-v1.pdf"]
    assert foreign_response.status_code == 200
    assert foreign_response.json()["total"] == 0
    assert foreign_owned_response.status_code == 200
    assert foreign_owned_response.json()["total"] == 1
    assert foreign_owned_response.json()["items"][0]["filename"] == "foreign.pdf"


def test_final_output_artifact_history_lists_final_acceptance_outputs_only() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-final-artifacts@example.com",
        slug="other-final-artifacts",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Final output artifact history",
        final_output={"final_output": "Approved delivery"},
    )
    other_task = Task(
        workspace_id=other_workspace.id,
        created_by_user_id=other_owner.id,
        title="Foreign final output artifact history",
    )
    session.add_all([task, other_task])
    session.flush()
    draft_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="draft",
        title="Draft",
        order_index=10,
        expected_artifacts=["draft"],
    )
    summary_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="manager-summary",
        title="Manager summary",
        order_index=20,
        expected_artifacts=["final_delivery"],
        review_policy={"reviewer": "user", "mode": "final_acceptance"},
    )
    revision_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="manager-summary-revision-1",
        title="Manager summary revision",
        order_index=30,
        expected_artifacts=["final_delivery"],
        review_policy={"reviewer": "user", "mode": "final_acceptance"},
    )
    foreign_step = TaskStep(
        workspace_id=other_workspace.id,
        task_id=other_task.id,
        work_package_id="manager-summary",
        title="Foreign manager summary",
        order_index=20,
        expected_artifacts=["final_delivery"],
        review_policy={"reviewer": "user", "mode": "final_acceptance"},
    )
    session.add_all([draft_step, summary_step, revision_step, foreign_step])
    session.flush()
    first_final = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=summary_step.id,
        work_package_id="manager-summary",
        version=1,
        artifact_type="document",
        filename="final-v1.pdf",
        content_type="application/pdf",
        size_bytes=10,
        checksum_sha256="1" * 64,
        storage_key="final-v1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    revision_final = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=revision_step.id,
        work_package_id="manager-summary-revision-1",
        version=1,
        artifact_type="document",
        filename="final-v2.pdf",
        content_type="application/pdf",
        size_bytes=12,
        checksum_sha256="2" * 64,
        storage_key="final-v2",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    draft = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=draft_step.id,
        work_package_id="draft",
        version=7,
        artifact_type="document",
        filename="draft.pdf",
        content_type="application/pdf",
        size_bytes=12,
        checksum_sha256="3" * 64,
        storage_key="draft",
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
    )
    foreign = Artifact(
        workspace_id=other_workspace.id,
        task_id=other_task.id,
        task_step_id=foreign_step.id,
        work_package_id="manager-summary",
        version=1,
        artifact_type="document",
        filename="foreign-final.pdf",
        content_type="application/pdf",
        size_bytes=12,
        checksum_sha256="4" * 64,
        storage_key="foreign-final",
        created_at=datetime(2026, 1, 4, tzinfo=UTC),
    )
    session.add_all([first_final, revision_final, draft, foreign])
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/artifacts/final-output/history?task_id={task.id}",
        headers=_headers(owner.id),
    )
    foreign_task_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/artifacts/final-output/history?task_id={task.id}",
        headers=_headers(other_owner.id),
    )
    foreign_owned_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/artifacts/final-output/history?task_id={other_task.id}",
        headers=_headers(other_owner.id),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["final_output"] == {"final_output": "Approved delivery"}
    assert payload["final_work_package_ids"] == ["manager-summary", "manager-summary-revision-1"]
    assert payload["total"] == 2
    assert payload["latest_artifact_id"] == str(revision_final.id)
    assert payload["latest_version"] == 1
    assert [item["filename"] for item in payload["items"]] == ["final-v2.pdf", "final-v1.pdf"]
    assert foreign_task_response.status_code == 200
    assert foreign_task_response.json()["total"] == 0
    assert foreign_task_response.json()["final_output"] is None
    assert foreign_owned_response.status_code == 200
    assert foreign_owned_response.json()["items"][0]["filename"] == "foreign-final.pdf"
