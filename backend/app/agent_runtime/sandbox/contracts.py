from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SandboxManifest:
    """Product-owned workspace declaration passed to a provider SDK."""

    run_id: UUID
    root: str
    files: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SandboxSession:
    session_id: str
    root: str
    backend: str
    persistent: bool = False


class SandboxBackend(Protocol):
    """Execution boundary shared by all provider adapters."""

    name: str

    def acquire(self, manifest: SandboxManifest) -> SandboxSession: ...

    def release(self, session: SandboxSession) -> None: ...
