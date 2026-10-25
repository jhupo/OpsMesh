from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .identity import UserIdentity


@dataclass(frozen=True)
class SandboxContext:
    user: UserIdentity
    config: dict[str, Any]
    root_dir: Path


class SandboxFactory(Protocol):
    def __call__(
        self,
        client: Any,
        options: Any,
        *,
        manifest: Any | None = None,
    ) -> AbstractAsyncContextManager[Any]: ...


@dataclass(frozen=True)
class SessionContext:
    id: str
    scope: str
    root_dir: Path
    sandbox_root: str
    history: Any
    sandbox: SandboxFactory
