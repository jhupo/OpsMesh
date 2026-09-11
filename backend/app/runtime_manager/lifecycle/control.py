from typing import Protocol
from uuid import UUID

from backend.app.runtime_manager.core.contracts import RuntimeLimits
from backend.app.runtimes.models import WorkspaceRuntime


class RuntimeLifecycleControl(Protocol):
    """Lifecycle requests implemented by queued API control or direct worker control."""

    def create_runtime(
        self, *, workspace_id: UUID, template_id: UUID, name: str,
        limits: RuntimeLimits | None, network_disabled: bool,
        runtime_space_id: UUID | None = None,
    ) -> WorkspaceRuntime | None: ...

    def start_runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime | None: ...
    def stop_runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime | None: ...
