import os
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.app.agents.profiles.models import AgentProfile
from backend.app.identity.authorization.context import AuthenticatedUser, WorkspaceContext
from backend.app.identity.users.models import User
from backend.app.orchestration.conversations.maintenance import ConversationMaintenanceService
from backend.app.orchestration.conversations.models import (
    Conversation,
    ConversationEvent,
    ConversationExecution,
    ConversationTurn,
)
from backend.app.orchestration.conversations.service import ConversationService
from backend.app.shared.db.base import Base
from backend.app.workspaces.management.models import Workspace
from backend.app.workspaces.members.models import WorkspaceMember
from backend.tests.test_postgres_scheduler_concurrency import _temporary_postgres_schema

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL URL required"
)


def test_concurrent_message_acceptance_and_transactional_dispatch() -> None:
    with _temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            user = User(email=f"{uuid4()}@example.com", display_name="Owner")
            workspace = Workspace(owner=user, name="Test", slug=uuid4().hex, settings={})
            member = WorkspaceMember(workspace=workspace, user=user, role="owner")
            session.add_all([user, workspace, member])
            session.flush()
            agent = AgentProfile(
                workspace_id=workspace.id, name="Assistant", role="assistant", instructions="Help"
            )
            session.add(agent)
            session.flush()
            conversation = Conversation(
                workspace_id=workspace.id,
                created_by_user_id=user.id,
                title="Test",
                mode="agent",
                agent_profile_id=agent.id,
            )
            session.add(conversation)
            session.commit()
            wid, uid, mid, cid = workspace.id, user.id, member.id, conversation.id
        barrier = threading.Barrier(2)

        def send() -> object:
            with factory() as session:
                context = WorkspaceContext(
                    AuthenticatedUser.from_model(session.get(User, uid)),
                    session.get(Workspace, wid),
                    session.get(WorkspaceMember, mid),
                )
                barrier.wait(timeout=10)
                return ConversationService(session).send(context, cid, "hello", "same-request").id

        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = list(executor.map(lambda _: send(), range(2)))
        assert first == second
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 1
            assert session.scalar(select(func.count()).select_from(ConversationEvent)) == 1
            assert ConversationMaintenanceService(session).process_one()
            session.rollback()
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(ConversationExecution)) == 0
        barrier = threading.Barrier(2)

        def dispatch() -> bool:
            with factory() as session:
                barrier.wait(timeout=10)
                handled = ConversationMaintenanceService(session).process_one()
                session.commit()
                return handled

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda _: dispatch(), range(2)))
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(ConversationExecution)) == 1
            assert session.scalar(select(ConversationTurn.status)) == "running"
