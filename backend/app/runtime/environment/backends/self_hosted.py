from uuid import UUID

from backend.app.runtime.contracts import SandboxManifest, SandboxSession
from backend.app.runtime.environment.backends.contracts import RuntimeBackendCapabilities
from backend.app.runtime.environment.contracts import RuntimeProjectFilesystem
from backend.app.runtime.environment.models import WorkspaceRuntime


class SelfHostedRuntimeBackend:
    """Capability declaration for execution delegated to an enrolled worker."""

    capabilities = RuntimeBackendCapabilities(True, True, False, False)

    def project_filesystem(
        self,
        runtime: WorkspaceRuntime,
        run_id: UUID,
    ) -> RuntimeProjectFilesystem | None:
        return None

    def sandbox_session(
        self,
        manifest: SandboxManifest,
        runtime: WorkspaceRuntime,
    ) -> SandboxSession:
        raise RuntimeError("Self-hosted sandbox sessions are provided by the enrolled worker")
