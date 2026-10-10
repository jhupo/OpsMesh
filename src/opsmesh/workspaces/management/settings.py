from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.agents.providers.models import ModelProviderCredential
from opsmesh.governance.reviews.approval_config import approval_configuration


def scheduler_settings(settings: dict[str, object]) -> dict[str, object]:
    raw_scheduler = settings.get("scheduler") if isinstance(settings, dict) else None
    if not isinstance(raw_scheduler, dict):
        return {}
    return dict(raw_scheduler)


def semantic_resource_review_settings(settings: dict[str, object]) -> dict[str, object]:
    return approval_configuration(settings).model_dump(mode="json")


def validate_resource_review_settings(
    session: Session,
    *,
    workspace_id: UUID,
    settings: object,
) -> None:
    if not isinstance(settings, dict):
        return
    config = approval_configuration(settings)
    credential_id = config.model.model_provider_credential_id if config.model else None
    if credential_id is None:
        return
    credential = session.scalar(
        select(ModelProviderCredential).where(
            ModelProviderCredential.workspace_id == workspace_id,
            ModelProviderCredential.id == credential_id,
        )
    )
    if credential is None:
        raise ValueError("Resource review model provider credential not found")
    if credential.status != "active":
        raise ValueError("Resource review model provider credential is not active")
