"""Generated extensions API group; regenerate instead of editing by hand.

Produced by scripts/generate_sdk_api.py from docs/openapi.json and
sdk/python/codegen.toml; the platform OpenAPI is the HTTP contract.
Stable operation ids of this group:

- ``extensions.deployments.get``
- ``extensions.deployments.stop``
- ``extensions.installations.list``
- ``extensions.installations.create``
- ``extensions.installations.get``
- ``extensions.installations.disable``
- ``extensions.installations.enable``
- ``extensions.installations.uninstall``
- ``extensions.instances.create``
- ``extensions.instances.get``
- ``extensions.instances.update``
- ``extensions.deployments.create``
- ``extensions.invocations.create``
- ``extensions.invocations.get``
- ``extensions.invocations.cancel``
- ``extensions.invocations.host_calls.list``
- ``extensions.invocations.host_calls.reconcile``
- ``extensions.invocations.reconcile``
- ``extensions.state.cleanup``
- ``extensions.state.cleanup_preview``
- ``extensions.state.restore``
- ``extensions.state.snapshot``
- ``extensions.state.export``
"""

from __future__ import annotations

import builtins
from uuid import UUID

from pydantic import BaseModel

from opsmesh_sdk.transport.contracts import (
    GroupChannel,
    TransportRequest,
    decode_array,
)


class CleanupPreview(BaseModel):
    entries: list[dict[str, object]]
    entry_count: int
    namespace_id: UUID
    preview_digest: str
    revision: int
    used_bytes: int


class CleanupRequest(BaseModel):
    expected_revision: int
    preview_digest: str


class CleanupResult(BaseModel):
    cleaned: bool


class DeploymentRequest(BaseModel):
    expected_revision: int
    operation_id: str


class DeploymentView(BaseModel):
    error_code: str | None
    id: UUID
    instance_id: UUID
    instance_revision: int
    revision: int
    status: str


class ExtensionReconcileRequest(BaseModel):
    evidence: dict[str, object]
    expected_revision: int
    outcome: str
    result: dict[str, object] | None = None


class HostCallView(BaseModel):
    id: UUID
    operation_id: str
    revision: int
    status: str


class InstallRequest(BaseModel):
    operation_id: str
    permission_ceiling: list[str] | None = None
    version_id: UUID


class InstallationView(BaseModel):
    artifact: str | None
    error_code: str | None
    id: UUID
    namespace: str
    permission_ceiling: list[str]
    revision: int
    status: str
    version_id: UUID


class InstanceConfiguration(BaseModel):
    configuration: dict[str, object] | None = None
    configuration_id: UUID | None = None
    environment_id: UUID | None = None
    installation_id: UUID
    model_connections: list[str] | None = None
    permissions: list[str] | None = None
    state_limit_bytes: int = 10485760


class InstanceUpdate(BaseModel):
    configuration: dict[str, object] | None = None
    configuration_id: UUID | None = None
    environment_id: UUID | None = None
    expected_revision: int
    installation_id: UUID
    model_connections: list[str] | None = None
    permissions: list[str] | None = None
    state_limit_bytes: int = 10485760


class InstanceView(BaseModel):
    configuration: dict[str, object] | None = None
    configuration_id: UUID | None = None
    environment_id: UUID | None = None
    id: UUID
    installation_id: UUID
    model_connections: list[str] | None = None
    owner_id: UUID
    permissions: list[str] | None = None
    revision: int
    state_limit_bytes: int = 10485760
    status: str


class InvocationView(BaseModel):
    cancel_requested: bool
    effects: list[str]
    error_code: str | None
    export_id: str
    id: UUID
    installation_id: UUID
    instance_id: UUID
    namespace_id: UUID
    result: object | None = None
    revision: int
    status: str


class InvokeRequest(BaseModel):
    arguments: dict[str, object] | None = None
    export_id: str
    operation_id: str


class RevisionRequest(BaseModel):
    expected_revision: int


class StatePage(BaseModel):
    entries: list[dict[str, object]]
    next_cursor: str | None


class StateRestoreResult(BaseModel):
    restored_entries: int


class StateSnapshot(BaseModel):
    entries: list[dict[str, object]]
    installation_id: UUID
    revision: int
    version_id: UUID


class StateRestoreRequest(BaseModel):
    expected_revision: int
    snapshot: StateSnapshot


class ExtensionsDeploymentsClient:
    """Generated client for the ``extensions.deployments`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def get(self, deployment_id: UUID) -> DeploymentView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/extensions/deployments/{deployment_id}",
            ),
            self._channel.context(),
        )
        return DeploymentView.model_validate(response.body)

    async def stop(self, deployment_id: UUID, request: RevisionRequest) -> DeploymentView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/extensions/deployments/{deployment_id}/stop",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return DeploymentView.model_validate(response.body)

    async def create(self, instance_id: UUID, request: DeploymentRequest) -> DeploymentView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/extensions/instances/{instance_id}/deployments",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return DeploymentView.model_validate(response.body)


class ExtensionsInstallationsClient:
    """Generated client for the ``extensions.installations`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(self, *, limit: int = 50, offset: int = 0) -> builtins.list[InstallationView]:
        query = {
            name: str(value)
            for name, value in (
                ("limit", limit),
                ("offset", offset),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/extensions/installations",
                query=query,
            ),
            self._channel.context(),
        )
        return [InstallationView.model_validate(item) for item in decode_array(response)]

    async def create(self, request: InstallRequest) -> InstallationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/extensions/installations",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return InstallationView.model_validate(response.body)

    async def get(self, installation_id: UUID) -> InstallationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/extensions/installations/{installation_id}",
            ),
            self._channel.context(),
        )
        return InstallationView.model_validate(response.body)

    async def disable(self, installation_id: UUID, request: RevisionRequest) -> InstallationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/extensions/installations/{installation_id}/disable",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return InstallationView.model_validate(response.body)

    async def enable(self, installation_id: UUID, request: RevisionRequest) -> InstallationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/extensions/installations/{installation_id}/enable",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return InstallationView.model_validate(response.body)

    async def uninstall(self, installation_id: UUID, request: RevisionRequest) -> InstallationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/extensions/installations/{installation_id}/uninstall",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return InstallationView.model_validate(response.body)


class ExtensionsInstancesClient:
    """Generated client for the ``extensions.instances`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def create(self, request: InstanceConfiguration) -> InstanceView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/extensions/instances",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return InstanceView.model_validate(response.body)

    async def get(self, instance_id: UUID) -> InstanceView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/extensions/instances/{instance_id}",
            ),
            self._channel.context(),
        )
        return InstanceView.model_validate(response.body)

    async def update(self, instance_id: UUID, request: InstanceUpdate) -> InstanceView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/extensions/instances/{instance_id}",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return InstanceView.model_validate(response.body)


class ExtensionsInvocationsHostCallsClient:
    """Generated client for the ``extensions.invocations.host_calls`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(self, invocation_id: UUID) -> builtins.list[HostCallView]:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/extensions/invocations/{invocation_id}/host-calls",
            ),
            self._channel.context(),
        )
        return [HostCallView.model_validate(item) for item in decode_array(response)]

    async def reconcile(
        self,
        invocation_id: UUID,
        receipt_id: UUID,
        request: ExtensionReconcileRequest,
    ) -> HostCallView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/extensions/invocations/{invocation_id}/host-calls/{receipt_id}/reconcile",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return HostCallView.model_validate(response.body)


class ExtensionsInvocationsClient:
    """Generated client for the ``extensions.invocations`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.host_calls = ExtensionsInvocationsHostCallsClient(channel)

    async def create(self, instance_id: UUID, request: InvokeRequest) -> InvocationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/extensions/instances/{instance_id}/invocations",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return InvocationView.model_validate(response.body)

    async def get(self, invocation_id: UUID) -> InvocationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/extensions/invocations/{invocation_id}",
            ),
            self._channel.context(),
        )
        return InvocationView.model_validate(response.body)

    async def cancel(self, invocation_id: UUID, request: RevisionRequest) -> InvocationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/extensions/invocations/{invocation_id}/cancel",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return InvocationView.model_validate(response.body)

    async def reconcile(
        self,
        invocation_id: UUID,
        request: ExtensionReconcileRequest,
    ) -> InvocationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/extensions/invocations/{invocation_id}/reconcile",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return InvocationView.model_validate(response.body)


class ExtensionsStateClient:
    """Generated client for the ``extensions.state`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def cleanup(self, namespace_id: UUID, request: CleanupRequest) -> CleanupResult:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/extensions/namespaces/{namespace_id}/cleanup",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return CleanupResult.model_validate(response.body)

    async def cleanup_preview(self, namespace_id: UUID) -> CleanupPreview:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/extensions/namespaces/{namespace_id}/cleanup-preview",
            ),
            self._channel.context(),
        )
        return CleanupPreview.model_validate(response.body)

    async def restore(self, namespace_id: UUID, request: StateRestoreRequest) -> StateRestoreResult:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/extensions/namespaces/{namespace_id}/restore",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return StateRestoreResult.model_validate(response.body)

    async def snapshot(self, namespace_id: UUID) -> StateSnapshot:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/extensions/namespaces/{namespace_id}/snapshot",
            ),
            self._channel.context(),
        )
        return StateSnapshot.model_validate(response.body)

    async def export(self, namespace_id: UUID, *, cursor: str = '', limit: int = 100) -> StatePage:
        query = {
            name: str(value)
            for name, value in (
                ("cursor", cursor),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/extensions/namespaces/{namespace_id}/state",
                query=query,
            ),
            self._channel.context(),
        )
        return StatePage.model_validate(response.body)


class ExtensionsClient:
    """The extensions API group."""

    def __init__(self, channel: GroupChannel) -> None:
        self.deployments = ExtensionsDeploymentsClient(channel)
        self.installations = ExtensionsInstallationsClient(channel)
        self.instances = ExtensionsInstancesClient(channel)
        self.invocations = ExtensionsInvocationsClient(channel)
        self.state = ExtensionsStateClient(channel)


def create_extensions_client(channel: GroupChannel) -> ExtensionsClient:
    """Create the extensions group client over one composed platform channel."""
    return ExtensionsClient(channel)


__all__ = [
    "CleanupPreview",
    "CleanupRequest",
    "CleanupResult",
    "DeploymentRequest",
    "DeploymentView",
    "ExtensionReconcileRequest",
    "ExtensionsClient",
    "ExtensionsDeploymentsClient",
    "ExtensionsInstallationsClient",
    "ExtensionsInstancesClient",
    "ExtensionsInvocationsClient",
    "ExtensionsInvocationsHostCallsClient",
    "ExtensionsStateClient",
    "HostCallView",
    "InstallRequest",
    "InstallationView",
    "InstanceConfiguration",
    "InstanceUpdate",
    "InstanceView",
    "InvocationView",
    "InvokeRequest",
    "RevisionRequest",
    "StatePage",
    "StateRestoreRequest",
    "StateRestoreResult",
    "StateSnapshot",
    "create_extensions_client",
]
