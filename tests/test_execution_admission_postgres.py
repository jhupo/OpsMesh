"""Update admission is atomic with business writes and preserves safe controls."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.identity.authorization.context import AuthenticatedUser, WorkspaceContext
from opsmesh.orchestration.conversations.models import Conversation, ConversationTurn
from opsmesh.orchestration.conversations.service import ConversationService
from opsmesh.orchestration.runs.control import RunControlService
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task
from opsmesh.orchestration.tasks.service import TaskCreateCommand, WorkspaceTaskService
from opsmesh.platform.updates.admission import require_execution_admission
from opsmesh.platform.updates.models import PlatformInstallation
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.errors import DomainError
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.database import product_session
from tests.fixtures.temporal import seed_workspace


def test_maintenance_rejects_new_chat_and_tasks_but_allows_duplicates_and_cancellation():
    session = product_session()
    user, workspace = seed_workspace(session)
    member = session.scalar(select(WorkspaceMember))
    profile = AgentProfile(workspace_id=workspace.id, name="Assistant", role="assistant")
    session.add(profile)
    session.flush()
    conversation = Conversation(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Maintenance",
        mode="agent",
        agent_profile_id=profile.id,
    )
    session.add(conversation)
    session.commit()
    context = WorkspaceContext(AuthenticatedUser.from_model(user), workspace, member)
    chat = ConversationService(session)
    accepted = chat.send(context, conversation.id, "Accepted", "accepted-once")
    session.get(PlatformInstallation, 1).maintenance = True
    session.commit()
    assert chat.send(context, conversation.id, "Accepted", "accepted-once").id == accepted.id
    with pytest.raises(DomainError) as rejected:
        chat.send(context, conversation.id, "New", "new-execution")
    assert rejected.value.status_code == 503 and rejected.value.code == "platform_maintenance"
    session.rollback()
    with pytest.raises(DomainError, match="platform is being updated"):
        WorkspaceTaskService(session).create_task(
            workspace.id, user.id, TaskCreateCommand(title="New task")
        )
    session.rollback()
    assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 1
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Previously accepted",
        status="running",
    )
    session.add(task)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=profile.id,
        status="running",
        started_at=datetime.now(UTC),
    )
    session.add(run)
    session.commit()
    RunControlService(session).cancel_run(
        workspace_id=workspace.id, run_id=run.id, actor_user_id=user.id
    )
    assert run.status == task.status == "cancelled"
    assert (
        session.scalar(
            select(func.count())
            .select_from(TemporalOutboxEntry)
            .where(TemporalOutboxEntry.operation == "cancel")
        )
        == 1
    )


def test_updater_cannot_enter_maintenance_before_admission_transaction_commits():
    session = product_session()
    factory = sessionmaker(session.get_bind(), expire_on_commit=False)
    require_execution_admission(session)

    def update():
        with factory.begin() as updating:
            installation = updating.get(PlatformInstallation, 1, with_for_update=True)
            installation.maintenance = True

    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(update)
        # PostgreSQL reports the blocked updater without relying on a timing sleep.
        with factory.begin() as observer:
            from sqlalchemy import text

            observer.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(OperationalError):
                observer.get(PlatformInstallation, 1, with_for_update=True)
        assert not waiting.done()
        session.commit()
        waiting.result(timeout=5)
    with factory() as denied, pytest.raises(DomainError):
        require_execution_admission(denied)
