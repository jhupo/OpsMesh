from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.costs.service import CostAccountingService
from backend.app.memory.configuration import EMBEDDING_DIMENSIONS
from backend.app.memory.models import (
    WorkspaceMemoryConfiguration,
    WorkspaceMemoryEmbeddingEvent,
    WorkspaceMemoryEntry,
)
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.model_providers.provider_keys import (
    canonical_model_provider,
    is_openai_compatible_provider,
)
from backend.app.secrets.service import SecretEncryptionService


class MemoryEmbeddingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class MemoryEmbeddingResult:
    vector: list[float]
    input_tokens: int


@dataclass(frozen=True, slots=True)
class MemoryEmbeddingWork:
    workspace_id: UUID
    memory_entry_id: UUID
    configuration_version: int
    embedding_generation: int
    content_fingerprint: str
    text: str
    provider: str
    credential_id: UUID
    model: str
    dimensions: int


class MemoryEmbeddingProvider(Protocol):
    provider_name: str
    model: str
    dimensions: int

    def embed(self, text: str) -> MemoryEmbeddingResult: ...


OpenAIClientFactory = Callable[..., OpenAI]


class OpenAIMemoryEmbeddingProvider:
    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None,
        model: str,
        dimensions: int,
        timeout_seconds: float,
        provider_name: str,
        client_factory: OpenAIClientFactory = OpenAI,
    ) -> None:
        self.provider_name = canonical_model_provider(provider_name)
        self.model = model
        self.dimensions = dimensions
        self._client = client_factory(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=2,
        )

    def embed(self, text: str) -> MemoryEmbeddingResult:
        try:
            response = self._client.embeddings.create(
                model=self.model,
                input=[text],
                dimensions=self.dimensions,
                encoding_format="float",
            )
        except APITimeoutError as exc:
            raise MemoryEmbeddingError(
                "embedding_provider_timeout",
                "Embedding provider timed out",
            ) from exc
        except APIConnectionError as exc:
            raise MemoryEmbeddingError(
                "embedding_provider_unavailable",
                "Embedding provider is unavailable",
            ) from exc
        except APIStatusError as exc:
            code = (
                "embedding_provider_rate_limited"
                if exc.status_code == 429
                else "embedding_provider_rejected"
            )
            raise MemoryEmbeddingError(code, "Embedding provider rejected the request") from exc
        except Exception as exc:
            raise MemoryEmbeddingError(
                "embedding_provider_failed",
                "Embedding provider failed",
            ) from exc
        if len(response.data) != 1:
            raise MemoryEmbeddingError(
                "embedding_response_invalid",
                "Embedding provider returned an invalid response count",
            )
        vector = [float(value) for value in response.data[0].embedding]
        _validate_vector(vector, self.dimensions)
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        return MemoryEmbeddingResult(vector=vector, input_tokens=max(input_tokens, 0))


class WorkspaceMemoryEmbeddingProviderResolver:
    def __init__(
        self,
        session: Session,
        secret_service: SecretEncryptionService,
        *,
        client_factory: OpenAIClientFactory = OpenAI,
    ) -> None:
        self._session = session
        self._secret_service = secret_service
        self._client_factory = client_factory

    def resolve(
        self,
        *,
        workspace_id: UUID,
        configuration: WorkspaceMemoryConfiguration,
    ) -> MemoryEmbeddingProvider:
        if not configuration.embedding_enabled:
            raise MemoryEmbeddingError(
                "memory_embeddings_disabled",
                "Workspace memory embeddings are disabled",
            )
        credential_id = configuration.embedding_credential_id
        if credential_id is None:
            raise MemoryEmbeddingError(
                "embedding_credential_missing",
                "Workspace memory embedding credential is missing",
            )
        credential = self._session.scalar(
            select(ModelProviderCredential).where(
                ModelProviderCredential.workspace_id == workspace_id,
                ModelProviderCredential.id == credential_id,
                ModelProviderCredential.status == "active",
            )
        )
        if credential is None or not is_openai_compatible_provider(credential.provider):
            raise MemoryEmbeddingError(
                "embedding_credential_unavailable",
                "Workspace memory embedding credential is unavailable",
            )
        CostAccountingService(self._session).assert_budget_available(
            workspace_id,
            provider=credential.provider,
            model=configuration.embedding_model,
        )
        payload = self._secret_service.decrypt_payload(credential.encrypted_api_key)
        api_key = payload.get("api_key")
        if not isinstance(api_key, str) or not api_key:
            raise MemoryEmbeddingError(
                "embedding_credential_invalid",
                "Workspace memory embedding credential is invalid",
            )
        return OpenAIMemoryEmbeddingProvider(
            api_key=api_key,
            base_url=credential.base_url,
            model=configuration.embedding_model,
            dimensions=configuration.embedding_dimensions,
            timeout_seconds=30,
            provider_name=credential.provider,
            client_factory=self._client_factory,
        )


class WorkspaceMemoryEmbeddingService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def prepare(
        self,
        *,
        workspace_id: UUID,
        memory_entry_id: UUID,
        embedding_generation: int,
    ) -> MemoryEmbeddingWork | None:
        configuration = self._session.scalar(
            select(WorkspaceMemoryConfiguration).where(
                WorkspaceMemoryConfiguration.workspace_id == workspace_id,
                WorkspaceMemoryConfiguration.embedding_enabled.is_(True),
            )
        )
        if configuration is None or configuration.embedding_credential_id is None:
            return None
        entry = self._session.scalar(
            select(WorkspaceMemoryEntry)
            .where(
                WorkspaceMemoryEntry.workspace_id == workspace_id,
                WorkspaceMemoryEntry.id == memory_entry_id,
                WorkspaceMemoryEntry.status == "active",
                WorkspaceMemoryEntry.memory_layer.in_(("episodic", "semantic")),
                WorkspaceMemoryEntry.embedding_generation == embedding_generation,
                WorkspaceMemoryEntry.embedding_status.in_(("pending", "queued", "failed")),
            )
            .with_for_update()
        )
        if entry is None:
            return None
        entry.embedding_status = "processing"
        entry.embedding_attempts += 1
        entry.embedding_last_error_code = None
        entry.embedding_processing_started_at = datetime.now(UTC)
        self._session.flush([entry])
        credential = self._session.get(
            ModelProviderCredential,
            configuration.embedding_credential_id,
        )
        provider = credential.provider if credential is not None else "openai"
        return MemoryEmbeddingWork(
            workspace_id=workspace_id,
            memory_entry_id=entry.id,
            configuration_version=configuration.version,
            embedding_generation=entry.embedding_generation,
            content_fingerprint=entry.content_fingerprint,
            text=f"{entry.title}\n\n{entry.content}",
            provider=canonical_model_provider(provider),
            credential_id=configuration.embedding_credential_id,
            model=configuration.embedding_model,
            dimensions=configuration.embedding_dimensions,
        )

    def complete(
        self,
        work: MemoryEmbeddingWork,
        result: MemoryEmbeddingResult,
    ) -> bool:
        _validate_vector(result.vector, work.dimensions)
        entry = self._locked_current_entry(work)
        if entry is None:
            return False
        entry.embedding = result.vector
        entry.embedding_model = work.model
        entry.embedding_content_fingerprint = work.content_fingerprint
        entry.embedding_status = "ready"
        entry.embedding_last_error_code = None
        entry.embedding_processing_started_at = None
        entry.embedded_at = datetime.now(UTC)
        self._session.add(
            WorkspaceMemoryEmbeddingEvent(
                workspace_id=work.workspace_id,
                memory_entry_id=work.memory_entry_id,
                credential_id=work.credential_id,
                configuration_version=work.configuration_version,
                embedding_generation=work.embedding_generation,
                provider=work.provider,
                model=work.model,
                dimensions=work.dimensions,
                status="completed",
                input_tokens=result.input_tokens,
            )
        )
        self._session.flush()
        return True

    def fail(self, work: MemoryEmbeddingWork, error_code: str) -> bool:
        entry = self._locked_current_entry(work)
        if entry is None:
            return False
        entry.embedding_status = "failed"
        entry.embedding_last_error_code = error_code[:120]
        entry.embedding_processing_started_at = None
        self._session.add(
            WorkspaceMemoryEmbeddingEvent(
                workspace_id=work.workspace_id,
                memory_entry_id=work.memory_entry_id,
                credential_id=work.credential_id,
                configuration_version=work.configuration_version,
                embedding_generation=work.embedding_generation,
                provider=work.provider,
                model=work.model,
                dimensions=work.dimensions,
                status="failed",
                error_code=error_code[:120],
            )
        )
        self._session.flush()
        return True

    def _locked_current_entry(
        self,
        work: MemoryEmbeddingWork,
    ) -> WorkspaceMemoryEntry | None:
        return self._session.scalar(
            select(WorkspaceMemoryEntry)
            .where(
                WorkspaceMemoryEntry.workspace_id == work.workspace_id,
                WorkspaceMemoryEntry.id == work.memory_entry_id,
                WorkspaceMemoryEntry.status == "active",
                WorkspaceMemoryEntry.embedding_generation == work.embedding_generation,
                WorkspaceMemoryEntry.content_fingerprint == work.content_fingerprint,
                WorkspaceMemoryEntry.embedding_status == "processing",
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )


def _validate_vector(vector: list[float], dimensions: int) -> None:
    if dimensions != EMBEDDING_DIMENSIONS or len(vector) != dimensions:
        raise MemoryEmbeddingError(
            "embedding_dimensions_invalid",
            f"Embedding vector must contain {EMBEDDING_DIMENSIONS} values",
        )
    if any(not math.isfinite(value) for value in vector):
        raise MemoryEmbeddingError(
            "embedding_values_invalid",
            "Embedding vector contains non-finite values",
        )
