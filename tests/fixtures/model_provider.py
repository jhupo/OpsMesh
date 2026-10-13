from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.agents.providers.credentials import ModelProviderCredentialCommandService
from opsmesh.shared.security.secrets import SecretEncryptionService


def seed_default_model_provider(session: Session, *, workspace_id: UUID, user_id: UUID) -> None:
    ModelProviderCredentialCommandService(
        session,
        SecretEncryptionService(
            secret="change-me-credential-encryption-secret",
            key_id="local",
        ),
    ).create(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        name="Unit test provider",
        provider="openai",
        api_key="sk-unit-test-provider",
        default_model="gpt-4.1",
        base_url=None,
        is_default=True,
    )
