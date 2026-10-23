"""Native SDK workspace layout and explicitly authorized host-directory mount."""

from pathlib import Path

from agents.sandbox import Manifest, Permissions, SandboxPathGrant
from agents.sandbox.entries import Dir

from opsmesh.execution.runtime.schemas import SANDBOX_WORKDIR

# The directory is shared data, not an authorization boundary. Private native
# histories belong to service-controlled storage, never this tool-writable mount.
WORKING_DIRECTORIES = ("files", "artifacts", "skills", "tools", "mcp", "memory")


def workspace_manifest() -> Manifest:
    return Manifest(
        root=SANDBOX_WORKDIR,
        entries={
            # The inherited ACL grants the service group access; its mask must
            # remain writable. Other users have no ACL permissions.
            name: Dir(permissions=Permissions.from_mode(0o770))
            for name in WORKING_DIRECTORIES
        },
    )


def trusted_manifest(path: Path) -> Manifest:
    # The SDK Docker backend requires host grants outside its own workspace root.
    # This private helper root is separate from the persistent Agent workspace.
    return Manifest(
        root="/tmp/opsmesh_runtime",
        extra_path_grants=(
            SandboxPathGrant(path=SANDBOX_WORKDIR, host_path=str(path), read_only=False),
        ),
    )
