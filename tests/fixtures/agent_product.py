"""PostgreSQL-backed product authorization fixtures."""

from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from opsmesh.agents.providers.credentials import ModelProviderCredentialCommandService
from opsmesh.identity.users.models import User
from opsmesh.shared.db.base import Base
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.database import product_engine


def _session() -> Session:
    engine = product_engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(
    session: Session,
    *,
    with_default_provider: bool = True,
) -> tuple[User, Workspace]:
    user = User(email="owner@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme", slug="acme", settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    if with_default_provider:
        _seed_default_model_provider(
            session,
            workspace_id=workspace.id,
            user_id=user.id,
        )
    return user, workspace


def _seed_default_model_provider(
    session: Session,
    *,
    workspace_id: UUID,
    user_id: UUID,
    api_key: str = "sk-unit-test-provider",
) -> None:
    ModelProviderCredentialCommandService(
        session,
        SecretEncryptionService(secret="change-me-credential-encryption-secret", key_id="local"),
    ).create(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        name="Unit test provider",
        provider="openai",
        api_key=api_key,
        default_model="gpt-4.1",
        base_url=None,
        is_default=True,
    )
