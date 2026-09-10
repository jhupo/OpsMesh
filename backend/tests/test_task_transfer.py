from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.schemas.tasks import TaskTransferResponse
from backend.app.db.base import Base
from backend.app.db.models import (  # noqa: F401
    AgentRun,
    AgentTeam,
    AgentTeamMember,
    Task,
    TaskStep,
    TaskTransfer,
    User,
    Workspace,
)
from backend.app.tasks.ownership import task_owner_can_execute_step
from backend.app.tasks.transfers import (
    TaskTransferCommand,
    TaskTransferDecision,
    TaskTransferError,
    TaskTransferService,
)


def _fixture() -> tuple[Session, User, Task, AgentProfile, AgentProfile, TaskStep]:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    user = User(email="owner@example.com", display_name="Owner")
    session.add(user)
    session.flush()
    workspace = Workspace(
        owner_user_id=user.id,
        name="Transfer workspace",
        slug="transfer-workspace",
    )
    session.add(workspace)
    session.flush()
    source = AgentProfile(name="Source manager", role="manager", workspace_id=workspace.id)
    target = AgentProfile(name="Target manager", role="manager", workspace_id=workspace.id)
    session.add_all([source, target])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Delivery",
        manager_agent_profile_id=source.id,
    )
    session.add(team)
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=source.id,
                team_role="manager",
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=target.id,
                team_role="manager",
            ),
        ]
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        owner_agent_profile_id=source.id,
        title="Ship transfer support",
        description="Finish the delivery work.",
        status="queued",
        team_snapshot={"team": {"manager_agent_profile_id": str(source.id)}},
        project_plan={
            "planner_agent_profile_id": str(source.id),
            "work_packages": [
                {
                    "package_id": "manager-summary",
                    "assigned_agent_profile_id": str(source.id),
                    "review_policy": {"mode": "final_acceptance"},
                }
            ],
        },
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="manager-summary",
        assigned_agent_profile_id=source.id,
        title="Manager summary",
        description="Integrate the delivery.",
        review_policy={"mode": "final_acceptance"},
        status="queued",
    )
    session.add(step)
    session.commit()
    return session, user, task, source, target, step


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()


def test_task_transfer_captures_package_and_changes_owner() -> None:
    session, user, task, source, target, step = _fixture()
    service = TaskTransferService(session)
    command = TaskTransferCommand(
        target_agent_profile_id=target.id,
        source_agent_profile_id=source.id,
        reason="The original manager is unavailable.",
        idempotency_key="transfer-1",
    )

    transfer = service.request_transfer(
        workspace_id=task.workspace_id,
        task_id=task.id,
        actor_user_id=user.id,
        command=command,
    )
    assert transfer is not None
    assert transfer.status == "pending"
    assert transfer.handoff_package["objective"]["title"] == task.title
    assert transfer.handoff_package["steps"][0]["work_package_id"] == "manager-summary"
    assert TaskTransferResponse.model_validate(transfer).status == "pending"
    assert session.scalar(
        select(TaskTransfer.id).where(TaskTransfer.id == transfer.id)
    ) == transfer.id

    accepted = service.accept_transfer(
        workspace_id=task.workspace_id,
        task_id=task.id,
        transfer_id=transfer.id,
        actor_user_id=user.id,
        decision=TaskTransferDecision(reason="Accepted by the delivery team."),
    )
    assert accepted is not None
    assert accepted.status == "accepted"
    session.refresh(task)
    session.refresh(step)
    assert task.owner_agent_profile_id == target.id
    assert task.owner_version == 2
    assert step.assigned_agent_profile_id == target.id
    assert task_owner_can_execute_step(task, step)


def test_task_transfer_idempotency_returns_original_request() -> None:
    session, user, task, source, target, _ = _fixture()
    command = TaskTransferCommand(
        target_agent_profile_id=target.id,
        source_agent_profile_id=source.id,
        reason="Move ownership.",
        idempotency_key="same-key",
    )
    service = TaskTransferService(session)
    first = service.request_transfer(
        workspace_id=task.workspace_id,
        task_id=task.id,
        actor_user_id=user.id,
        command=command,
    )
    second = service.request_transfer(
        workspace_id=task.workspace_id,
        task_id=task.id,
        actor_user_id=user.id,
        command=command,
    )
    assert first is not None
    assert second is not None
    assert second.id == first.id


def test_task_transfer_rejects_active_execution() -> None:
    session, user, task, source, target, step = _fixture()
    session.add(
        AgentRun(
            workspace_id=task.workspace_id,
            task_id=task.id,
            task_step_id=step.id,
            agent_profile_id=source.id,
            status="running",
            input={},
        )
    )
    session.commit()
    command = TaskTransferCommand(
        target_agent_profile_id=target.id,
        source_agent_profile_id=source.id,
        reason="Move after stopping execution.",
        idempotency_key="active-transfer",
    )
    try:
        TaskTransferService(session).request_transfer(
            workspace_id=task.workspace_id,
            task_id=task.id,
            actor_user_id=user.id,
            command=command,
        )
    except TaskTransferError as exc:
        assert exc.code == "task_transfer_active_run"
    else:
        raise AssertionError("active execution must block ownership transfer")
