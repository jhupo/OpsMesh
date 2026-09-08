from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.files.content import (
    DEFAULT_AGENT_FILE_READ_MAX_BYTES,
    DEFAULT_AGENT_READABLE_CONTENT_TYPES,
    WorkspaceFileContentReader,
)
from backend.app.files.storage import ObjectStorage
from backend.app.tools.context import ToolContext
from backend.app.tools.errors import ToolResourceNotFoundError
from backend.app.tools.product_tools.files import WorkspaceFileProductTools
from backend.app.tools.product_tools.mailbox import AgentMailboxProductTools
from backend.app.tools.product_tools.memory import WorkspaceMemoryProductTools


class ProductToolService(
    WorkspaceFileProductTools,
    WorkspaceMemoryProductTools,
    AgentMailboxProductTools,
):
    def __init__(
        self,
        session: Session,
        *,
        storage: ObjectStorage | None = None,
        max_file_read_bytes: int = DEFAULT_AGENT_FILE_READ_MAX_BYTES,
        readable_content_types: frozenset[str] = DEFAULT_AGENT_READABLE_CONTENT_TYPES,
    ) -> None:
        self._session = session
        self._workspace_file_content_reader = (
            WorkspaceFileContentReader(
                storage,
                max_bytes=max_file_read_bytes,
                allowed_content_types=readable_content_types,
            )
            if storage is not None
            else None
        )

    def _sender_agent_profile_id(self, context: ToolContext) -> UUID:
        run = self._run_for_context(context)
        if run is None or run.agent_profile_id is None:
            raise ToolResourceNotFoundError("Agent run is not bound to an agent profile")
        agent = self._session.get(AgentProfile, run.agent_profile_id)
        if agent is None or agent.workspace_id != context.workspace_id:
            raise ToolResourceNotFoundError("Agent not found in workspace")
        return run.agent_profile_id
