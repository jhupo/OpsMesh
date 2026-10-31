"""Operational state deliberately excludes credentials and event bodies."""

from datetime import datetime
from typing import Any, Protocol

from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract


class PluginDeploymentPolicy(OpsMeshContract):
    revision: int = 0
    enabled: dict[str, bool] = Field(default_factory=dict)


class PluginInstanceStatus(OpsMeshContract):
    instance_id: str
    seen_at: datetime
    policy_revision: int
    plugins: dict[str, str]
    errors: dict[str, str] = Field(default_factory=dict)
    stale: bool = False


class PluginOperations(Protocol):
    """Public plugin management, excluding trusted instance reconciliation.

    Python plugins are trusted application code; this surface is an API contract,
    not a security sandbox against arbitrary Python object introspection.
    """

    async def inventory(self, *, actor_id: str) -> list[dict[str, Any]]: ...
    async def policy(self, *, actor_id: str) -> PluginDeploymentPolicy: ...
    async def set_enabled(
        self,
        module: str,
        enabled: bool,
        *,
        actor_id: str,
        expected_revision: int,
        cascade: bool = False,
    ) -> PluginDeploymentPolicy: ...
    async def instance_status(self, *, actor_id: str) -> list[PluginInstanceStatus]: ...
