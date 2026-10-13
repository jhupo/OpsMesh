import os
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.identity.users.models import User
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.collaboration.transfers import (
    TaskTransferCommand,
    TaskTransferDecision,
    TaskTransferError,
    TaskTransferService,
)
from opsmesh.orchestration.tasks.control.ownership import task_owner_can_execute_step
from opsmesh.orchestration.tasks.models import Task, TaskStep, TaskTransfer
from opsmesh.orchestration.tasks.schemas.management import TaskTransferResponse
from opsmesh.shared.db.base import Base
from opsmesh.teams.management.models import AgentTeam, AgentTeamMember
from tests.fixtures.database import temporary_postgres_schema
from tests.fixtures.temporal import seed_workspace

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


@contextmanager
def _fixture() -> Iterator[tuple[Session, User, Task, AgentProfile, AgentProfile, TaskStep]]:
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            user, workspace = seed_workspace(session)
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
            yield session, user, task, source, target, step


def test_task_transfer_captures_package_and_changes_owner() -> None:
    with _fixture() as (session, user, task, source, target, step):
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
        assert (
            session.scalar(select(TaskTransfer.id).where(TaskTransfer.id == transfer.id))
            == transfer.id
        )

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
        assert step.assigned_agent_profile_id == source.id
        assert task_owner_can_execute_step(task, step)


def test_task_transfer_idempotency_returns_original_request() -> None:
    with _fixture() as (session, user, task, source, target, _):
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
    with _fixture() as (session, user, task, source, target, step):
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
