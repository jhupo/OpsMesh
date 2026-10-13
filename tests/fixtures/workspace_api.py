import json
from collections.abc import Generator
from uuid import UUID

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.identity.users.models import User
from opsmesh.main import create_app
from opsmesh.runtime.temporal.contracts import DispatchRequest
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.base import Base
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.redis.dependencies import get_redis_client
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.agent_product import _seed_default_model_provider
from tests.fixtures.database import product_engine
from tests.fixtures.execution import test_agent_id

TOKEN = "test-token"
ADMIN_TOKEN = "admin-token"


def _client():
    engine = product_engine()
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    session = sessions()
    redis = fakeredis.FakeRedis(decode_responses=True)
    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            platform_admin_token=ADMIN_TOKEN,
            database_url=engine.url.render_as_string(hide_password=False),
            runtime_allowed_images=["python@sha256:" + "0" * 64],
        )
    )

    def database_session() -> Generator[Session, None, None]:
        with sessions() as request_session:
            yield request_session

    app.dependency_overrides[get_db_session] = database_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    app.dependency_overrides[get_redis_client] = lambda: redis
    return TestClient(app), session


def _seed_workspace(
    session: Session,
    *,
    role: str,
    email: str = "owner@example.com",
    slug: str = "owner-space",
    with_provider: bool = False,
) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role=role)
    session.add_all([user, workspace, membership])
    session.commit()
    if with_provider:
        _seed_default_model_provider(session, workspace_id=workspace.id, user_id=user.id)
    return user, workspace


def _headers(user_id: object) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-User-ID": str(user_id),
    }


def _api_agent_id(session: Session, workspace_id: UUID) -> UUID:
    identifier = test_agent_id(session, workspace_id)
    session.commit()
    return identifier


def _sse_data_events(text: str, event_name: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for block in text.strip().split("\n\n"):
        name = None
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            if line.startswith("data: "):
                data_lines.append(line.removeprefix("data: "))
        if name == event_name and data_lines:
            events.append(json.loads("\n".join(data_lines)))
    return events


def _pending_dispatch_count(session: Session, workspace_id: UUID) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(TemporalOutboxEntry)
            .where(
                TemporalOutboxEntry.workspace_id == workspace_id,
                TemporalOutboxEntry.published_at.is_(None),
            )
        )
        or 0
    )


def _dispatch_for(session: Session, resource_id: UUID) -> DispatchRequest | None:
    rows = session.scalars(select(TemporalOutboxEntry).order_by(TemporalOutboxEntry.id)).all()
    return next(
        (
            request
            for row in rows
            if (request := DispatchRequest.model_validate(row.payload)).execution is not None
            and request.execution.resource_id == resource_id
        ),
        None,
    )
