from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from opsmesh.runtime.contracts import SandboxManifest, SandboxSession
from opsmesh.runtime.instances.contracts import RuntimeProjectFilesystem
from opsmesh.runtime.instances.models import WorkspaceRuntime


@dataclass(frozen=True, slots=True)
class RuntimeBackendCapabilities:
    mcp_stdio: bool
    asynchronous_jobs: bool
    managed_container_lifecycle: bool
    direct_project_files: bool


class RuntimeBackend(Protocol):
    """Resource capabilities exposed by one runtime provider.

    Agent tools and MCP adapters are composed above this boundary; a resource backend
    never constructs product-domain services.
    """

    capabilities: RuntimeBackendCapabilities

    def project_filesystem(
        self,
        runtime: WorkspaceRuntime,
        run_id: UUID,
    ) -> RuntimeProjectFilesystem | None: ...

    def sandbox_session(
        self,
        manifest: SandboxManifest,
        runtime: WorkspaceRuntime,
    ) -> SandboxSession: ...
