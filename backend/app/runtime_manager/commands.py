from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.runtime_manager.manager_factory import RuntimeManagerFactory
from backend.app.runtime_manager.queries import RuntimeControlQueryService
from backend.app.runtimes.models import RuntimeCommand, RuntimeEvent, WorkspaceRuntime


class RuntimeCommandService:
    def __init__(self, session: Session, manager_factory: RuntimeManagerFactory) -> None:
        self._session = session
        self._manager_factory = manager_factory

    def execute_command(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        command: list[str],
    ) -> RuntimeCommand:
        return self._manager_factory.require().execute_command(
            workspace_id=workspace_id,
            runtime=runtime,
            command=command,
        )

    def queue_command(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        command: list[str],
    ) -> RuntimeCommand:
        record = RuntimeCommand(
            workspace_id=workspace_id,
            workspace_runtime_id=runtime.id,
            runtime_space_id=runtime.runtime_space_id,
            command=command,
            status="queued",
        )
        self._session.add(record)
        self._session.flush()
        self._session.add(
            RuntimeEvent(
                workspace_id=workspace_id,
                workspace_runtime_id=runtime.id,
                runtime_space_id=runtime.runtime_space_id,
                event_type="runtime.command.queued",
                message=" ".join(command),
                event_metadata={"runtime_id": str(runtime.id), "command_id": str(record.id)},
                created_at=datetime.now(UTC),
            )
        )
        self._session.commit()
        self._session.refresh(record)
        return record

    def execute_queued_command(
        self,
        *,
        workspace_id: UUID,
        runtime: WorkspaceRuntime,
        runtime_id: UUID,
        command_id: UUID,
        command: list[str],
    ) -> RuntimeCommand | None:
        record = RuntimeControlQueryService(self._session).get_command(
            workspace_id=workspace_id,
            runtime_id=runtime_id,
            command_id=command_id,
        )
        if record is None:
            return None
        if record.status != "queued":
            return record
        return self._manager_factory.require().execute_existing_command(
            workspace_id=workspace_id,
            runtime=runtime,
            record=record,
            command=command,
        )
