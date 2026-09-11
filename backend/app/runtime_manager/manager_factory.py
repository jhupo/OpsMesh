from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.runtime_manager.core.contracts import DockerRuntimeClient
from backend.app.runtime_manager.manager import RuntimeManager


class RuntimeManagerFactory:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        docker_client: DockerRuntimeClient | None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._docker_client = docker_client
        self._manager: RuntimeManager | None = None

    def require(self) -> RuntimeManager:
        if self._docker_client is None:
            raise RuntimeError("Runtime manager execution requires a worker-injected Docker client")
        if self._manager is None:
            self._manager = RuntimeManager(
                self._session,
                self._docker_client,
                managed_host_roots=[Path(self._settings.storage_root).resolve() / "runtimes"],
            )
        return self._manager
