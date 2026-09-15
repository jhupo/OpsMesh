from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.core.security.secrets import SecretEncryptionService
from backend.app.domains.capabilities.tools.events import ProductToolEventRecorder
from backend.app.domains.capabilities.tools.files import WorkspaceFileProductTools
from backend.app.domains.capabilities.tools.mailbox import AgentMailboxProductTools
from backend.app.domains.capabilities.tools.memory import WorkspaceMemoryProductTools
from backend.app.domains.workspace.storage.artifact_persistence import ArtifactPersistenceService
from backend.app.domains.workspace.storage.content import (
    DEFAULT_AGENT_FILE_READ_MAX_BYTES,
    DEFAULT_AGENT_READABLE_CONTENT_TYPES,
    WorkspaceFileContentReader,
)
from backend.app.domains.workspace.storage.storage import ObjectStorage


class ProductToolService:
    def __init__(
        self,
        session: Session,
        *,
        storage: ObjectStorage | None = None,
        max_file_read_bytes: int = DEFAULT_AGENT_FILE_READ_MAX_BYTES,
        readable_content_types: frozenset[str] = DEFAULT_AGENT_READABLE_CONTENT_TYPES,
        memory_embedding_secret_service: SecretEncryptionService | None = None,
    ) -> None:
        events = ProductToolEventRecorder(session)
        artifact_persistence = (
            ArtifactPersistenceService(session, storage) if storage is not None else None
        )
        content_reader = (
            WorkspaceFileContentReader(
                storage,
                max_bytes=max_file_read_bytes,
                allowed_content_types=readable_content_types,
            )
            if storage is not None
            else None
        )
        self.files = WorkspaceFileProductTools(
            session,
            events,
            content_reader=content_reader,
            artifact_persistence=artifact_persistence,
        )
        self.memory = WorkspaceMemoryProductTools(
            session,
            events,
            memory_embedding_secret_service=memory_embedding_secret_service,
        )
        self.mailbox = AgentMailboxProductTools(session, events)
