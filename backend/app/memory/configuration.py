from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.memory.models import WorkspaceMemoryConfiguration, WorkspaceMemoryEntry
from backend.app.memory.policy import (
    HybridMemoryRetrievalPolicy,
    MemoryLifecyclePolicy,
    default_lifecycle_policy,
    default_retrieval_policy,
    hybrid_retrieval_policy,
    memory_lifecycle_policy,
)
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.model_providers.provider_keys import is_openai_compatible_provider

EMBEDDING_DIMENSIONS = 1_536


@dataclass(frozen=True, slots=True)
class MemoryConfigurationUpdate:
    embedding_enabled: bool
    embedding_credential_id: UUID | None
    embedding_model: str
    embedding_dimensions: int
    retrieval_policy: dict[str, object]
    lifecycle_policy: dict[str, object]
    expected_version: int


class MemoryConfigurationConflictError(ValueError):
    pass


class WorkspaceMemoryConfigurationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, workspace_id: UUID) -> WorkspaceMemoryConfiguration | None:
        return self._session.scalar(
            select(WorkspaceMemoryConfiguration).where(
                WorkspaceMemoryConfiguration.workspace_id == workspace_id
            )
        )

    def create_default(self, workspace_id: UUID) -> WorkspaceMemoryConfiguration:
        configuration = WorkspaceMemoryConfiguration(
            workspace_id=workspace_id,
            embedding_enabled=False,
            embedding_credential_id=None,
            embedding_model="text-embedding-3-small",
            embedding_dimensions=EMBEDDING_DIMENSIONS,
            retrieval_policy=default_retrieval_policy(),
            lifecycle_policy=default_lifecycle_policy(),
            version=1,
        )
        self._session.add(configuration)
        self._session.flush([configuration])
        return configuration

    def require(self, workspace_id: UUID) -> WorkspaceMemoryConfiguration:
        configuration = self.get(workspace_id)
        if configuration is None:
            raise ValueError("Workspace memory configuration not found")
        return configuration

    def update(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: MemoryConfigurationUpdate,
    ) -> WorkspaceMemoryConfiguration:
        configuration = self._session.scalar(
            select(WorkspaceMemoryConfiguration)
            .where(WorkspaceMemoryConfiguration.workspace_id == workspace_id)
            .with_for_update()
        )
        if configuration is None:
            raise ValueError("Workspace memory configuration not found")
        if configuration.version != command.expected_version:
            raise MemoryConfigurationConflictError(
                f"Workspace memory configuration version is {configuration.version}, "
                f"not {command.expected_version}"
            )
        model = command.embedding_model.strip()
        if not model or len(model) > 160:
            raise ValueError("Embedding model must be a non-empty string up to 160 characters")
        if command.embedding_dimensions != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Embedding dimensions must be {EMBEDDING_DIMENSIONS} for the current index"
            )
        credential = self._validate_credential(
            workspace_id=workspace_id,
            credential_id=command.embedding_credential_id,
            enabled=command.embedding_enabled,
        )
        retrieval = hybrid_retrieval_policy(command.retrieval_policy)
        lifecycle = memory_lifecycle_policy(command.lifecycle_policy)
        before = _configuration_payload(configuration)
        embedding_changed = (
            configuration.embedding_enabled != command.embedding_enabled
            or configuration.embedding_credential_id != command.embedding_credential_id
            or configuration.embedding_model != model
            or configuration.embedding_dimensions != command.embedding_dimensions
        )
        configuration.embedding_enabled = command.embedding_enabled
        configuration.embedding_credential_id = command.embedding_credential_id
        configuration.embedding_model = model
        configuration.embedding_dimensions = command.embedding_dimensions
        configuration.retrieval_policy = retrieval.model_dump(mode="json")
        configuration.lifecycle_policy = lifecycle.model_dump(mode="json")
        configuration.updated_by_user_id = actor_user_id
        configuration.version += 1
        if embedding_changed:
            self._reset_active_embeddings(workspace_id, enabled=command.embedding_enabled)
        self._session.flush([configuration])
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="memory.configuration_updated",
            target_type="workspace_memory_configuration",
            target_id=configuration.id,
            metadata={
                "before": before,
                "after": _configuration_payload(configuration),
                "credential_provider": credential.provider if credential is not None else None,
            },
        )
        return configuration

    def retry_failed_embeddings(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> int:
        configuration = self.require(workspace_id)
        if not configuration.embedding_enabled:
            raise ValueError("Workspace memory embeddings are disabled")
        result = self._session.execute(
            update(WorkspaceMemoryEntry)
            .where(
                WorkspaceMemoryEntry.workspace_id == workspace_id,
                WorkspaceMemoryEntry.embedding_status == "failed",
                WorkspaceMemoryEntry.status == "active",
                WorkspaceMemoryEntry.memory_layer.in_(("episodic", "semantic")),
            )
            .values(
                embedding_status="pending",
                embedding_generation=WorkspaceMemoryEntry.embedding_generation + 1,
                embedding_attempts=0,
                embedding_last_error_code=None,
                embedding_processing_started_at=None,
            )
        )
        count = int(getattr(result, "rowcount", 0) or 0)
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="memory.embedding_retry_requested",
            target_type="workspace_memory_configuration",
            target_id=configuration.id,
            metadata={"entries_reset": count, "configuration_version": configuration.version},
        )
        return count

    def policies(
        self,
        workspace_id: UUID,
    ) -> tuple[HybridMemoryRetrievalPolicy, MemoryLifecyclePolicy, int]:
        configuration = self.require(workspace_id)
        return (
            hybrid_retrieval_policy(configuration.retrieval_policy),
            memory_lifecycle_policy(configuration.lifecycle_policy),
            configuration.version,
        )

    def _validate_credential(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID | None,
        enabled: bool,
    ) -> ModelProviderCredential | None:
        if not enabled:
            if credential_id is not None:
                raise ValueError("Disabled embeddings cannot retain a credential")
            return None
        if credential_id is None:
            raise ValueError("Enabled embeddings require a model provider credential")
        credential = self._session.scalar(
            select(ModelProviderCredential).where(
                ModelProviderCredential.workspace_id == workspace_id,
                ModelProviderCredential.id == credential_id,
                ModelProviderCredential.status == "active",
            )
        )
        if credential is None:
            raise ValueError("Embedding credential not found in the workspace")
        if not is_openai_compatible_provider(credential.provider):
            raise ValueError("Embedding credential must use an OpenAI-compatible provider")
        return credential

    def _reset_active_embeddings(self, workspace_id: UUID, *, enabled: bool) -> None:
        status = "pending" if enabled else "not_applicable"
        self._session.execute(
            update(WorkspaceMemoryEntry)
            .where(
                WorkspaceMemoryEntry.workspace_id == workspace_id,
                WorkspaceMemoryEntry.status == "active",
                WorkspaceMemoryEntry.memory_layer.in_(("episodic", "semantic")),
            )
            .values(
                embedding=None,
                embedding_model=None,
                embedding_content_fingerprint=None,
                embedding_status=status,
                embedding_generation=WorkspaceMemoryEntry.embedding_generation + 1,
                embedding_attempts=0,
                embedding_last_error_code=None,
                embedding_processing_started_at=None,
                embedded_at=None,
            )
        )


def _configuration_payload(
    configuration: WorkspaceMemoryConfiguration,
) -> dict[str, object]:
    return {
        "embedding_enabled": configuration.embedding_enabled,
        "embedding_credential_id": (
            str(configuration.embedding_credential_id)
            if configuration.embedding_credential_id is not None
            else None
        ),
        "embedding_model": configuration.embedding_model,
        "embedding_dimensions": configuration.embedding_dimensions,
        "retrieval_policy": configuration.retrieval_policy,
        "lifecycle_policy": configuration.lifecycle_policy,
        "version": configuration.version,
    }


def initial_embedding_status(session: Session, workspace_id: UUID) -> str:
    enabled = session.scalar(
        select(WorkspaceMemoryConfiguration.embedding_enabled).where(
            WorkspaceMemoryConfiguration.workspace_id == workspace_id
        )
    )
    return "pending" if enabled is True else "not_applicable"
