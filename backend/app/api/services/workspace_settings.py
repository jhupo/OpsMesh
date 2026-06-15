from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.model_providers.models import ModelProviderCredential
from backend.app.reviews.constants import (
    PRIVATE_RESOURCE_REVIEW_SETTINGS_KEY,
    PUBLIC_RESOURCE_REVIEW_SETTINGS_KEY,
    RESOURCE_REVIEW_SETTINGS_KEY,
    SEMANTIC_REVIEW_SETTINGS_KEY,
)


def scheduler_settings(settings: dict[str, object]) -> dict[str, object]:
    raw_scheduler = settings.get("scheduler") if isinstance(settings, dict) else None
    if not isinstance(raw_scheduler, dict):
        return {}
    return dict(raw_scheduler)


def resource_review_settings(settings: dict[str, object]) -> dict[str, object]:
    raw_resource_review = settings.get(RESOURCE_REVIEW_SETTINGS_KEY)
    if not isinstance(raw_resource_review, dict):
        return {}
    raw_semantic = raw_resource_review.get(SEMANTIC_REVIEW_SETTINGS_KEY)
    raw_private = raw_resource_review.get(PRIVATE_RESOURCE_REVIEW_SETTINGS_KEY)
    raw_public = raw_resource_review.get(PUBLIC_RESOURCE_REVIEW_SETTINGS_KEY)
    summary: dict[str, object] = {}
    if isinstance(raw_semantic, dict):
        summary[SEMANTIC_REVIEW_SETTINGS_KEY] = dict(raw_semantic)
    if isinstance(raw_private, dict):
        summary[PRIVATE_RESOURCE_REVIEW_SETTINGS_KEY] = dict(raw_private)
    if isinstance(raw_public, dict):
        summary[PUBLIC_RESOURCE_REVIEW_SETTINGS_KEY] = dict(raw_public)
    return summary


def semantic_resource_review_settings(settings: dict[str, object]) -> dict[str, object]:
    summary = resource_review_settings(settings)
    semantic = summary.get(SEMANTIC_REVIEW_SETTINGS_KEY)
    return dict(semantic) if isinstance(semantic, dict) else {}


def validate_resource_review_settings(
    session: Session,
    *,
    workspace_id: UUID,
    settings: object,
) -> None:
    if not isinstance(settings, dict):
        return
    semantic = semantic_resource_review_settings(settings)
    credential_id = _uuid_or_none(semantic.get("model_provider_credential_id"))
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


def _uuid_or_none(value: object) -> UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise ValueError("Expected UUID value")
