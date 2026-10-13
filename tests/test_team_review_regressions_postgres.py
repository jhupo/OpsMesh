"""Product invariants for team configuration and atomic provider updates."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from opsmesh.agents.profiles.models import AgentProfile, AgentProfileVersion
from opsmesh.agents.sessions.models import (
    AgentSessionMetadata,
    SDKSessionMessageRecord,
    SDKSessionRecord,
)
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.identity.users.models import User
from opsmesh.orchestration.tasks.models import Task, TaskMessage, TaskStep
from opsmesh.teams.management.member_provider import update_team_member_model_provider
from opsmesh.teams.management.models import AgentTeam, AgentTeamMember
from opsmesh.teams.management.service import (
    TeamCreateCommand,
    TeamUpdateCommand,
    WorkspaceTeamService,
)
from opsmesh.teams.operations.command_center import TeamCommandCenterService
from opsmesh.teams.operations.schemas import AgentTeamMemberModelProviderUpdateRequest
from opsmesh.teams.projects.dashboard import TeamProjectDashboardService
from opsmesh.workspaces.management.models import Workspace
from tests.fixtures.database import product_session


@pytest.fixture
def session() -> Iterator[Session]:
    with product_session() as db:
        yield db


def _workspace(session: Session) -> tuple[User, Workspace]:
    user = User(email=f"review-{uuid4()}@example.test", display_name="Owner")
    workspace = Workspace(owner=user, name="Review", slug=f"review-{uuid4()}", settings={})
    session.add_all([user, workspace])
    session.commit()
    return user, workspace


@pytest.mark.parametrize("field", ["coordination_rules", "default_task_policy"])
def test_team_create_and_update_reject_raw_secret_without_persisting(
    session: Session, field: str
) -> None:
    _, workspace = _workspace(session)
    service = WorkspaceTeamService(session)
    command = TeamCreateCommand(name="Unsafe")
    object.__setattr__(command, field, {"nested": {"api_key": "sk-must-not-persist"}})
    with pytest.raises(ValueError):
        service.create_team(workspace.id, command)
    session.rollback()
    assert session.scalar(select(func.count()).select_from(AgentTeam)) == 0
    team = service.create_team(workspace.id, TeamCreateCommand(name="Allowed"))
    with pytest.raises(ValueError):
        service.update_team(
            workspace.id, team.id, TeamUpdateCommand({field: {"password": "do-not-store"}})
        )
    session.rollback()
    assert getattr(session.get(AgentTeam, team.id), field) == {}


def test_project_dashboard_counts_completed_steps_and_nonempty_tasks(session: Session) -> None:
    _, workspace = _workspace(session)
    team = WorkspaceTeamService(session).create_team(workspace.id, TeamCreateCommand(name="Team"))
    task = Task(
        workspace_id=workspace.id, agent_team_id=team.id, title="Delivery", status="running"
    )
    session.add(task)
    session.flush()
    session.add_all(
        [
            TaskStep(workspace_id=workspace.id, task_id=task.id, title="Done", status="completed"),
            TaskStep(workspace_id=workspace.id, task_id=task.id, title="Next", status="queued"),
        ]
    )
    session.commit()
    result = TeamProjectDashboardService(session).get_dashboard(
        workspace_id=workspace.id, team_id=team.id
    )
    assert result is not None
    assert result["summary"]["status_counts"] == {"running": 1}
    assert result["tasks"][0]["progress"]["step_status_counts"] == {
        "completed": 1,
        "queued": 1,
    }
    assert result["tasks"][0]["progress"]["completion_ratio"] == 0.5


@pytest.mark.parametrize("fail_message", [True, False])
def test_provider_change_commits_or_rolls_back_agent_session_versions_and_audit_together(
    session: Session, monkeypatch: pytest.MonkeyPatch, fail_message: bool
) -> None:
    from opsmesh.teams.management import member_provider

    owner, workspace = _workspace(session)
    agent = AgentProfile(workspace_id=workspace.id, name="Agent", role="worker", model="old-model")
    team = AgentTeam(workspace_id=workspace.id, name="Team")
    session.add_all([agent, team])
    session.flush()
    member = AgentTeamMember(
        workspace_id=workspace.id,
        agent_team_id=team.id,
        agent_profile_id=agent.id,
        team_role="worker",
    )
    history = AgentSessionMetadata(
        workspace_id=workspace.id,
        agent_team_id=team.id,
        agent_profile_id=agent.id,
        scope_type="team_agent",
        scope_id=f"{team.id}:{agent.id}",
        session_key=f"review-{uuid4()}",
    )
    session.add_all([member, history])
    session.flush()
    session.add(SDKSessionRecord(session_id=history.session_key))
    session.flush()
    session.add(
        SDKSessionMessageRecord(
            session_id=history.session_key, message_data='{"role":"user","content":"Keep"}'
        )
    )
    session.commit()
    before_version = agent.version

    def fail(**_: object) -> None:
        raise RuntimeError("message persistence failed")

    def change() -> None:
        update_team_member_model_provider(
            session,
            workspace_id=workspace.id,
            team_id=team.id,
            member_id=member.id,
            actor_user_id=owner.id,
            request=AgentTeamMemberModelProviderUpdateRequest(
                model="new-model", reset_session=True
            ),
        )

    if fail_message:
        monkeypatch.setattr(member_provider, "_append_team_model_provider_updated_message", fail)
        with pytest.raises(RuntimeError, match="message persistence failed"):
            change()
    else:
        change()
    session.expire_all()
    assert agent.model == ("old-model" if fail_message else "new-model")
    assert agent.version == before_version + (0 if fail_message else 1)
    assert session.scalar(select(func.count()).select_from(SDKSessionMessageRecord)) == int(
        fail_message
    )
    assert session.scalar(select(func.count()).select_from(AgentProfileVersion)) == int(
        not fail_message
    )
    audit_count = session.scalar(select(func.count()).select_from(AuditEvent))
    assert audit_count == 0 if fail_message else audit_count > 0
    assert ("last_reset" in history.session_metadata) is not fail_message


def test_team_and_agent_missing_provider_readiness_use_the_same_denial(session: Session) -> None:
    from opsmesh.agents.providers.views import agent_model_provider_summary
    from opsmesh.teams.provider_readiness.service import TeamProviderReadinessService

    _, workspace = _workspace(session)
    agent = AgentProfile(workspace_id=workspace.id, name="Agent", role="worker", model="gpt-test")
    team = AgentTeam(workspace_id=workspace.id, name="Team")
    session.add_all([agent, team])
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=agent.id,
            team_role="worker",
        )
    )
    session.commit()
    agent_readiness = agent_model_provider_summary(session, agent)
    team_readiness = TeamProviderReadinessService(session).get_readiness(
        workspace_id=workspace.id, team_id=team.id
    )
    assert agent_readiness["readiness_status"] == "blocked"
    assert team_readiness["members"][0]["readiness_status"] == "blocked"
    assert team_readiness["members"][0]["reasons"] == agent_readiness["reasons"]


def test_command_center_reports_bounded_task_scan_without_duplicate_manager_diagnostics(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opsmesh.orchestration.tasks.collaboration.manager_diagnostics import (
        TaskManagerDiagnosticsService,
    )

    _, workspace = _workspace(session)
    team = WorkspaceTeamService(session).create_team(workspace.id, TeamCreateCommand(name="Team"))
    session.add_all(
        [
            Task(workspace_id=workspace.id, agent_team_id=team.id, title=f"Task {index}")
            for index in range(6)
        ]
    )
    session.commit()
    original = TaskManagerDiagnosticsService.get_diagnostics
    calls = []

    def observe(self: TaskManagerDiagnosticsService, **kwargs: object) -> object:
        calls.append(kwargs["task_id"])
        return original(self, **kwargs)

    monkeypatch.setattr(TaskManagerDiagnosticsService, "get_diagnostics", observe)
    result = TeamCommandCenterService(session).get_command_center(
        workspace_id=workspace.id, team_id=team.id, queue_limit=1
    )
    assert result is not None
    assert result["summary"]["tasks_truncated"] is True
    assert result["summary"]["task_scan_limit"] == 4
    assert result["summary"]["scanned_task_count"] == 4
    assert result["summary"]["queue_truncated"] is True
    assert calls == []


@pytest.mark.parametrize("view", ["overview", "command_center"])
def test_team_diagnostic_select_count_is_constant_with_more_tasks(
    session: Session, view: str
) -> None:
    from opsmesh.teams.execution.overview import TeamExecutionOverviewService

    _, workspace = _workspace(session)
    team = WorkspaceTeamService(session).create_team(workspace.id, TeamCreateCommand(name="Team"))
    agent = AgentProfile(workspace_id=workspace.id, name="Worker", role="worker")
    session.add(agent)
    session.flush()
    workspace_id, team_id = workspace.id, team.id

    def add_tasks(count: int) -> None:
        for index in range(count):
            task = Task(workspace_id=workspace_id, agent_team_id=team_id, title=f"Task {index}")
            session.add(task)
            session.flush()
            session.add(
                TaskStep(
                    workspace_id=workspace_id,
                    task_id=task.id,
                    title="Step",
                    assigned_agent_profile_id=agent.id,
                )
            )
            session.add(
                TaskMessage(
                    workspace_id=workspace_id,
                    task_id=task.id,
                    message_type="note",
                    sequence=1,
                    agent_profile_id=agent.id,
                )
            )
        session.commit()

    def count_selects() -> int:
        queries = []

        def observe(*args: object) -> None:
            if str(args[2]).lstrip().upper().startswith("SELECT"):
                queries.append(str(args[2]))

        bind = session.get_bind()
        event.listen(bind, "before_cursor_execute", observe)
        try:
            if view == "overview":
                result = TeamExecutionOverviewService(session).get_overview(
                    workspace_id=workspace_id, team_id=team_id
                )
            else:
                result = TeamCommandCenterService(session).get_command_center(
                    workspace_id=workspace_id, team_id=team_id
                )
            assert result is not None
            return len(queries)
        finally:
            event.remove(bind, "before_cursor_execute", observe)

    add_tasks(2)
    small = count_selects()
    add_tasks(6)
    larger = count_selects()
    assert small > 0
    assert larger == small, (view, small, larger)


def test_batch_diagnostics_reject_foreign_task_and_reused_step_records(session: Session) -> None:
    from opsmesh.orchestration.tasks.collaboration.manager_diagnostics import (
        TaskManagerDiagnosticsService,
    )
    from opsmesh.orchestration.tasks.observation.execution import TaskExecutionDiagnosticsService

    _, workspace = _workspace(session)
    _, foreign = _workspace(session)
    task = Task(workspace_id=workspace.id, title="Local")
    foreign_task = Task(workspace_id=foreign.id, title="Foreign")
    session.add_all([task, foreign_task])
    session.flush()
    foreign_step = TaskStep(workspace_id=foreign.id, task_id=foreign_task.id, title="Foreign")
    session.add(foreign_step)
    session.commit()
    for service in (
        TaskManagerDiagnosticsService(session),
        TaskExecutionDiagnosticsService(session),
    ):
        with pytest.raises(ValueError, match="outside workspace"):
            service.diagnostics_for_tasks(workspace_id=workspace.id, tasks=[foreign_task])
        with pytest.raises(ValueError, match="outside task scope"):
            service.diagnostics_for_tasks(
                workspace_id=workspace.id, tasks=[task], steps=[foreign_step]
            )
