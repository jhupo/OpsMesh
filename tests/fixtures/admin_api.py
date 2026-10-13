from collections.abc import Generator

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.identity.users.models import User
from opsmesh.main import create_app
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.base import Base
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.redis.dependencies import get_redis_client
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.database import product_engine

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
        )
    )

    def database_session() -> Generator[Session, None, None]:
        with sessions() as request_session:
            yield request_session

    app.dependency_overrides[get_db_session] = database_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    app.dependency_overrides[get_redis_client] = lambda: redis
    return TestClient(app), session, redis


def _seed_workspace(
    session: Session,
    *,
    email: str = "owner@example.com",
    slug: str = "owner-space",
) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}
