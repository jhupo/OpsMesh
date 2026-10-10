from __future__ import annotations

from sqlalchemy.orm import Session

from opsmesh.capabilities.tools.events import ProductToolEventRecorder
from opsmesh.capabilities.tools.files import WorkspaceFileProductTools
from opsmesh.capabilities.tools.mailbox import AgentMailboxProductTools
from opsmesh.capabilities.tools.memory import WorkspaceMemoryProductTools
from opsmesh.platform.settings.policy import operational_configuration
from opsmesh.resources.artifacts.service import ArtifactPersistenceService
from opsmesh.resources.files.content import (
    WorkspaceFileContentReader,
)
from opsmesh.resources.storage.storage import ObjectStorage
from opsmesh.shared.security.secrets import SecretEncryptionService


class ProductToolService:
    def __init__(
        self,
        session: Session,
        *,
        storage: ObjectStorage | None = None,
        memory_embedding_secret_service: SecretEncryptionService | None = None,
    ) -> None:
        from opsmesh.capabilities.tools.conversations import ConversationProductTools

        events = ProductToolEventRecorder(session)
        artifact_persistence = (
            ArtifactPersistenceService(session, storage) if storage is not None else None
        )
        content_reader = (
            WorkspaceFileContentReader(
                storage,
                max_bytes=operational_configuration(session).files.agent_read_bytes,
                allowed_content_types=frozenset(
                    operational_configuration(session).files.readable_content_types
                ),
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
        self.conversations = ConversationProductTools(session)
