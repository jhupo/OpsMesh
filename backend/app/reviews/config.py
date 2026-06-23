from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.model_providers.service_models import ModelProviderUnavailableError
from backend.app.reviews.constants import (
    PRIVATE_RESOURCE_REVIEW_SETTINGS_KEY,
    RESOURCE_REVIEW_SETTINGS_KEY,
    SEMANTIC_REVIEW_SETTINGS_KEY,
)
from backend.app.reviews.utils import (
    _PRIVATE_REVIEW_DEFAULTS,
    _is_public_visibility,
    _review_model,
    _review_timeout_seconds,
    _uuid_or_none,
)
from backend.app.workspaces.models import Workspace


@dataclass(frozen=True)
class ResourceReviewConfig:
    credential_id: UUID | None
    model: str
    timeout_seconds: float


class ResourceReviewSettings:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enabled(self, workspace_id: UUID, resource_type: str, visibility: str) -> bool:
        if _is_public_visibility(visibility):
            return True
        config = self.resource_review_settings(workspace_id)
        raw_scope = config.get(PRIVATE_RESOURCE_REVIEW_SETTINGS_KEY)
        scope_config = raw_scope if isinstance(raw_scope, dict) else {}
        raw_value = scope_config.get(resource_type)
        if raw_value is None or not isinstance(raw_value, bool):
            return _PRIVATE_REVIEW_DEFAULTS.get(resource_type, True)
        return raw_value

    def semantic_config(self, workspace_id: UUID) -> ResourceReviewConfig:
        resource_review = self.resource_review_settings(workspace_id)
        raw_semantic = resource_review.get(SEMANTIC_REVIEW_SETTINGS_KEY)
        config = raw_semantic if isinstance(raw_semantic, dict) else {}
        if config.get("enabled") is False:
            raise ModelProviderUnavailableError("Semantic resource review is disabled")
        return ResourceReviewConfig(
            credential_id=_uuid_or_none(config.get("model_provider_credential_id")),
            model=_review_model(config.get("model")),
            timeout_seconds=_review_timeout_seconds(config.get("timeout_seconds")),
        )

    def resource_review_settings(self, workspace_id: UUID) -> dict[str, object]:
        workspace = self._session.get(Workspace, workspace_id)
        settings = workspace.settings if workspace is not None else {}
        raw = settings.get(RESOURCE_REVIEW_SETTINGS_KEY) if isinstance(settings, dict) else None
        return raw if isinstance(raw, dict) else {}
