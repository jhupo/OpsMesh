"""Persistent policy for deployed plugins, reconciled independently on each instance."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.plugin.manager import PluginManager
from opsmesh.framework.plugin.model import PluginState
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.accounts.api import AccountDirectory
from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation

from .api import PluginDeploymentPolicy, PluginInstanceStatus


class PluginDeploymentManagement:
    def __init__(
        self,
        database: AsyncDatabase,
        accounts: AccountDirectory,
        audit: AuditJournal,
        manager: PluginManager,
    ) -> None:
        self.policies = VersionedRecordStore(database, "plugin_deployment_policy")
        self.instances = VersionedRecordStore(database, "plugin_instance_status")
        self.accounts, self.audit, self.manager = accounts, audit, manager
        self.instance_id = uuid4().hex
        self._last_sync: datetime | None = None
        self._sync_lock = asyncio.Lock()

    async def _authorize(self, actor_id: str, *, write: bool = False) -> None:
        await self.accounts.authorize(
            await self.accounts.get_principal(actor_id),
            "plugins:manage" if write else "plugins:read",
            "*",
        )

    async def inventory(self, *, actor_id: str) -> list[dict[str, Any]]:
        await self._authorize(actor_id)
        policy = await self._policy()
        return [
            {
                "module": name,
                "state": plugin.state.value,
                "desired_enabled": policy.enabled.get(name, True),
                "dependencies": sorted(plugin.dependencies),
                "name": plugin.metadata.name if plugin.metadata else plugin.name,
                "description": plugin.metadata.description if plugin.metadata else "",
                "version": str(getattr(plugin.module, "__version__", "unspecified")),
            }
            for name, plugin in sorted(self.manager.plugins.items())
        ]

    async def _policy(self) -> PluginDeploymentPolicy:
        record = await self.policies.get("deployment")
        return (
            PluginDeploymentPolicy(revision=record.revision, enabled=record.data["enabled"])
            if record
            else PluginDeploymentPolicy()
        )

    async def policy(self, *, actor_id: str) -> PluginDeploymentPolicy:
        await self._authorize(actor_id)
        return await self._policy()

    async def set_enabled(
        self,
        module: str,
        enabled: bool,
        *,
        actor_id: str,
        expected_revision: int,
        cascade: bool = False,
    ) -> PluginDeploymentPolicy:
        await self._authorize(actor_id, write=True)
        if module not in self.manager.plugins:
            raise OpsMeshError("NOT_FOUND", "Only deployed, loaded plugins can be managed")
        prior = await self._policy()
        if prior.revision != expected_revision:
            raise OpsMeshError("CONFLICT", "Plugin policy changed")
        desired = {
            **prior.enabled,
            **{name: prior.enabled.get(name, True) for name in self.manager.plugins},
        }
        desired[module] = enabled
        if not enabled and cascade:
            changed = True
            while changed:
                changed = False
                for name, plugin in self.manager.plugins.items():
                    if desired[name] and any(
                        not desired.get(dep, False) for dep in plugin.dependencies
                    ):
                        desired[name] = False
                        changed = True
        for name, plugin in self.manager.plugins.items():
            if desired[name] and any(not desired.get(dep, False) for dep in plugin.dependencies):
                raise OpsMeshError("CONFLICT", "Enabled plugin requires a disabled dependency")
        async with audited_operation(
            self.audit,
            "plugins.set_enabled",
            module,
            actor_id,
            details={"enabled": enabled, "cascade": cascade},
        ):
            record = (
                await self.policies.create("deployment", {"enabled": desired})
                if not prior.revision
                else await self.policies.update("deployment", {"enabled": desired}, prior.revision)
            )
        await self.synchronize(force=True)
        return PluginDeploymentPolicy(revision=record.revision, enabled=desired)

    async def instance_status(self, *, actor_id: str) -> list[PluginInstanceStatus]:
        await self._authorize(actor_id)
        cutoff = datetime.now(UTC) - timedelta(seconds=30)
        return [
            PluginInstanceStatus.model_validate(
                {**item.data, "stale": datetime.fromisoformat(item.data["seen_at"]) < cutoff}
            )
            for item in await self.instances.list()
        ]

    async def synchronize(self, *, force: bool = False) -> None:
        async with self._sync_lock:
            await self._synchronize(force=force)

    async def _synchronize(self, *, force: bool) -> None:
        now = datetime.now(UTC)
        if not force and self._last_sync and (now - self._last_sync).total_seconds() < 5:
            return
        policy = await self._policy()
        errors = {
            name: "NOT_DEPLOYED"
            for name, enabled in policy.enabled.items()
            if enabled and name not in self.manager.plugins
        }
        for name, plugin in self.manager.plugins.items():
            if not policy.enabled.get(name, True) and plugin.enabled:
                self.manager.disable(name, cascade=True)
        for name, plugin in self.manager.plugins.items():
            missing = plugin.dependencies - self.manager.plugins.keys()
            unavailable = missing or any(
                not self.manager.plugins[dep].enabled for dep in plugin.dependencies
            )
            if unavailable and plugin.enabled:
                self.manager.disable(name, cascade=True)
            if missing:
                errors[name] = "DEPENDENCY_NOT_DEPLOYED"
        pending = {
            name
            for name, plugin in self.manager.plugins.items()
            if policy.enabled.get(name, True) and plugin.state == PluginState.DISABLED
        }
        while pending:
            ready = {
                name
                for name in pending
                if all(
                    dep in self.manager.plugins and self.manager.plugins[dep].enabled
                    for dep in self.manager.plugins[name].dependencies
                )
            }
            if not ready:
                for name in pending:
                    errors.setdefault(name, "DEPENDENCY_DISABLED")
                break
            for name in ready:
                self.manager.enable(name)
            pending -= ready
        errors.update(
            {
                name: "LOAD_FAILED"
                for name, plugin in self.manager.plugins.items()
                if plugin.state == PluginState.FAILED
            }
        )
        state = PluginInstanceStatus(
            instance_id=self.instance_id,
            seen_at=now,
            policy_revision=policy.revision,
            plugins={name: plugin.state.value for name, plugin in self.manager.plugins.items()},
            errors=errors,
        )
        prior = await self.instances.get(self.instance_id)
        if prior:
            await self.instances.update(
                self.instance_id, state.model_dump(mode="json"), prior.revision
            )
        else:
            await self.instances.create(self.instance_id, state.model_dump(mode="json"))
        self._last_sync = now
