from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agents.models import AgentProfile
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.planning.member_matching import MemberMatchingService
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task, TaskStep
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_member_matching_ranks_by_role_and_skill_weight() -> None:
    designer_id = uuid4()
    developer_id = uuid4()
    snapshot = {
        "members": [
            {
                "id": str(uuid4()),
                "agent_profile_id": str(developer_id),
                "team_role": "frontend_engineer",
                "skill_weights": {"react": 0.9},
                "max_concurrent_tasks": 2,
                "accepts_tasks": True,
            },
            {
                "id": str(uuid4()),
                "agent_profile_id": str(designer_id),
                "team_role": "ui_designer",
                "skill_weights": {"figma": 1.0, "design_system": 0.8},
                "max_concurrent_tasks": 2,
                "accepts_tasks": True,
            },
        ]
    }

    match = MemberMatchingService().match(
        team_snapshot=snapshot,
        required_role="ui_designer",
        required_skills=["figma"],
    )

    assert match is not None
    assert match.agent_profile_id == designer_id
    assert "role_exact" in match.reasons
    assert "skill:figma" in match.reasons


def test_member_matching_skips_members_at_concurrency_limit() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    busy_agent = AgentProfile(workspace_id=workspace.id, name="Busy Dev", role="frontend_engineer")
    free_agent = AgentProfile(workspace_id=workspace.id, name="Free Dev", role="frontend_engineer")
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Existing work")
    session.add_all([busy_agent, free_agent, task])
    session.flush()
    busy_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=busy_agent.id,
        status="running",
        title="Busy step",
    )
    session.add(busy_step)
    session.flush()
    session.add(
        AgentRun(
            workspace_id=workspace.id,
            task_id=task.id,
            task_step_id=busy_step.id,
            agent_profile_id=busy_agent.id,
            status=RunStatus.RUNNING.value,
            input={},
        )
    )
    session.commit()
    snapshot = {
        "members": [
            {
                "id": str(uuid4()),
                "agent_profile_id": str(busy_agent.id),
                "team_role": "frontend_engineer",
                "skill_weights": {"react": 1.0},
                "max_concurrent_tasks": 1,
                "accepts_tasks": True,
            },
            {
                "id": str(uuid4()),
                "agent_profile_id": str(free_agent.id),
                "team_role": "frontend_engineer",
                "skill_weights": {"react": 0.8},
                "max_concurrent_tasks": 1,
                "accepts_tasks": True,
            },
        ]
    }

    match = MemberMatchingService(session).match(
        team_snapshot=snapshot,
        required_role="frontend_engineer",
        required_skills=["react"],
        workspace_id=workspace.id,
    )

    assert match is not None
    assert match.agent_profile_id == free_agent.id


def test_member_matching_penalizes_leadership_for_execution_work() -> None:
    ceo_id = uuid4()
    engineer_id = uuid4()
    snapshot = {
        "members": [
            {
                "id": str(uuid4()),
                "agent_profile_id": str(ceo_id),
                "team_role": "ceo",
                "responsibilities": ["Own strategy and executive escalation"],
                "skill_weights": {"python": 1.0},
                "max_concurrent_tasks": 5,
                "accepts_tasks": True,
            },
            {
                "id": str(uuid4()),
                "agent_profile_id": str(engineer_id),
                "team_role": "backend_engineer",
                "responsibilities": ["Build backend services in Python"],
                "skill_weights": {"python": 0.6},
                "max_concurrent_tasks": 1,
                "accepts_tasks": True,
            },
        ]
    }

    match = MemberMatchingService().match(
        team_snapshot=snapshot,
        required_role="backend_engineer",
        required_skills=["python"],
    )

    assert match is not None
    assert match.agent_profile_id == engineer_id
    assert "leadership_execution_penalty" not in match.reasons


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(session: Session) -> tuple[User, Workspace]:
    user = User(email="owner@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme", slug="acme", settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
