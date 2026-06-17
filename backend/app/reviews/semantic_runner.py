from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.model_providers.service import (
    ModelProviderCredentialService,
    ModelProviderUnavailableError,
)
from backend.app.reviews.config import ResourceReviewSettings
from backend.app.reviews.llm import LlmResourceReviewer
from backend.app.reviews.llm_review import (
    llm_unavailable_review,
    merge_policy_and_llm_reviews,
    review_skipped,
)
from backend.app.reviews.models import ResourceReview
from backend.app.reviews.scanner import ReviewScanner
from backend.app.secrets.service import SecretEncryptionService


class SemanticResourceReviewRunner:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings
        self._review_settings = ResourceReviewSettings(session)

    def review_with_policy_signals(
        self,
        *,
        workspace_id: UUID | None,
        resource_type: str,
        visibility: str,
        resource: dict[str, object],
        scanner: ReviewScanner,
    ) -> ResourceReview:
        policy_review = scanner.result(reviewer="policy_guardrail")
        if workspace_id is not None and (
            not self._review_settings.enabled(workspace_id, resource_type, visibility)
        ):
            return review_skipped(policy_review, resource_type=resource_type, visibility=visibility)
        if workspace_id is None:
            return llm_unavailable_review(
                policy_review,
                ValueError("Workspace id is required for semantic resource review"),
            )
        if self._settings is None:
            return llm_unavailable_review(
                policy_review,
                ValueError("Settings are required for semantic resource review"),
            )
        review_config = self._review_settings.semantic_config(workspace_id)
        try:
            provider = self._model_provider_service().resolve_for_review(
                workspace_id=workspace_id,
                credential_id=review_config.credential_id,
                review_model=review_config.model,
            )
            llm_review = LlmResourceReviewer().review(
                provider=provider,
                resource_type=resource_type,
                resource=resource,
                static_signals=policy_review.signals,
                timeout_seconds=review_config.timeout_seconds,
            )
        except ModelProviderUnavailableError as exc:
            return llm_unavailable_review(policy_review, exc)
        except (ValueError, RuntimeError, OSError) as exc:
            return llm_unavailable_review(policy_review, exc)
        return merge_policy_and_llm_reviews(policy_review, llm_review)

    def _model_provider_service(self) -> ModelProviderCredentialService:
        if self._settings is None:
            raise ModelProviderUnavailableError("Review settings are unavailable")
        return ModelProviderCredentialService(
            self._session,
            SecretEncryptionService(
                secret=self._settings.credential_encryption_secret,
                key_id=self._settings.credential_encryption_key_id,
                previous_secrets=self._settings.credential_encryption_previous_secrets,
            ),
        )
