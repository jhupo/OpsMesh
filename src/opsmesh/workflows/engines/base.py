"""Engine-neutral lifecycle adapter injected for a caller-owned transaction."""

from typing import Protocol
from uuid import UUID


class WorkflowEngine(Protocol):
    name: str

    def start(self, run_id: UUID) -> None: ...
    def cancel(self, run_id: UUID) -> None: ...
    def signal(self, run_id: UUID, signal_id: UUID) -> None: ...
