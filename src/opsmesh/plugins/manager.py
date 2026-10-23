"""Prepare complete generations before publishing; retain old code for in-flight users."""

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from opsmesh_sdk import PluginContext, PluginManifest

from opsmesh.plugins.base import PreparedResource
from opsmesh.plugins.loader import LoadedPlugin, load_plugin
from opsmesh.plugins.packages import PluginPackages
from opsmesh.plugins.registry import PluginRegistry
from opsmesh.shared.security.encryption import SecretCipher

logger = logging.getLogger(__name__)
ViewFactory = Callable[[tuple[PluginContext, ...], Mapping[str, PreparedResource]], dict[str, Any]]


async def _finish_mutation[T](operation: Awaitable[T]) -> T:
    # A cancelled request must not abandon a thread importing code or committing files.
    task = asyncio.ensure_future(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


@dataclass(frozen=True)
class PluginSnapshot:
    revision: str = ""
    plugins: tuple[LoadedPlugin, ...] = ()
    resources: dict[str, PreparedResource] = field(default_factory=dict)
    views: dict[str, Any] = field(default_factory=dict)


class PluginManager:
    def __init__(
        self,
        packages: PluginPackages,
        data_root: Path,
        registry: PluginRegistry,
        views: ViewFactory,
        *,
        role: str,
        cipher: SecretCipher,
    ) -> None:
        self.packages, self.data_root, self.registry = packages, data_root, registry
        self.views, self.cipher, self.role = views, cipher, role
        self.current = PluginSnapshot()
        self.retired: list[PluginSnapshot] = []
        self.apply: Callable[[PluginSnapshot], Awaitable[None]] | None = None
        self.mutations = asyncio.Lock()
        self.host_id = uuid4().hex
        self.error: str | None = None
        try:
            self.data_root.mkdir(parents=True)
        except FileExistsError:
            if not self.data_root.is_dir():
                raise
        else:
            self.data_root.chmod(0o2770)

    def revision(self, records: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()

    def prepare(
        self,
        records: dict[str, Any],
        *,
        directories: Mapping[str, Path] | None = None,
    ) -> PluginSnapshot:
        loaded: list[LoadedPlugin] = []
        prepared: dict[str, PreparedResource] = {}
        try:
            for record in records.values():
                if record.get("enabled") is not True:
                    continue
                manifest = PluginManifest.model_validate(record["manifest"])
                config = record.get("config", {})
                if "encrypted_config" in record:
                    config = self.cipher.decrypt_payload(
                        record["encrypted_config"], key_id=record["config_key_id"]
                    )
                loaded.append(
                    load_plugin(
                        manifest,
                        (directories or {}).get(
                            manifest.id, self.packages.root / manifest.id / manifest.version
                        ),
                        self.data_root,
                        config,
                    )
                )
            contexts = tuple(item.context for item in loaded)
            prepared = self.registry.prepare(contexts)
            views = self.views(contexts, prepared)
            return PluginSnapshot(self.revision(records), tuple(loaded), prepared, views)
        except BaseException:
            try:
                self.registry.release(prepared)
            finally:
                for item in reversed(loaded):
                    item.unload()
            raise

    def discard(self, snapshot: PluginSnapshot) -> None:
        try:
            self.registry.release(snapshot.resources)
        finally:
            for plugin in reversed(snapshot.plugins):
                plugin.unload()

    async def publish(self, snapshot: PluginSnapshot) -> None:
        try:
            if self.apply is not None:
                await self.apply(snapshot)
        except BaseException:
            # A feature rollback can itself fail, leaving live objects from this
            # attempted generation. Keep its code available until shutdown too.
            self.retired.append(snapshot)
            raise
        if self.current.revision:
            # Existing Agents and callbacks can still hold this generation's objects.
            # Keep their namespaces and package files until process shutdown.
            self.retired.append(self.current)
        self.current, self.error = snapshot, None
        self.acknowledge()

    async def reload(self) -> None:
        await _finish_mutation(self._reload())

    async def _reload(self) -> None:
        async with self.mutations:
            records = await asyncio.to_thread(self.packages.read)
            if self.revision(records) != self.current.revision:
                snapshot = await asyncio.to_thread(self.prepare, records)
                await self.publish(snapshot)
            self.error = None
            self.acknowledge()

    async def change(self, mutate: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        return await _finish_mutation(self._change(mutate))

    async def _change(self, mutate: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        async with self.mutations:

            def stage() -> tuple[PluginSnapshot, dict[str, Any], dict[str, Any]]:
                with self.packages.lock():
                    before = self.packages.read()
                    after = mutate(json.loads(json.dumps(before)))
                    snapshot = self.prepare(after)
                    try:
                        self.packages.write(after)
                    except BaseException:
                        self.discard(snapshot)
                        raise
                    return snapshot, before, after

            snapshot, before, after = await asyncio.to_thread(stage)
            try:
                await self.publish(snapshot)
            except BaseException:

                def restore() -> None:
                    with self.packages.lock():
                        if self.packages.read() == after:
                            self.packages.write(before)

                await asyncio.to_thread(restore)
                raise
            return self.status()

    def acknowledge(self) -> None:
        root = self.data_root / ".hosts"
        try:
            root.mkdir()
        except FileExistsError:
            if not root.is_dir():
                raise
        else:
            root.chmod(0o2770)
        target = root / f"{self.host_id}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "host_id": self.host_id,
                    "role": self.role,
                    "revision": self.current.revision,
                    "error": self.error,
                    "seen_at": time.time(),
                }
            ),
            encoding="utf-8",
        )
        temporary.replace(target)

    def status(self) -> dict[str, Any]:
        desired = self.revision(self.packages.read())
        hosts = []
        for path in (self.data_root / ".hosts").glob("*.json"):
            try:
                value = json.loads(path.read_bytes())
                if time.time() - value["seen_at"] < 15:
                    hosts.append(value)
            except (OSError, ValueError, KeyError):
                continue
        roles = {
            item["role"] for item in hosts if item["revision"] == desired and not item["error"]
        }
        return {
            "revision": desired,
            "local_revision": self.current.revision,
            "hosts": hosts,
            "applied": {"api", "worker", "gateway"} <= roles
            and all(item["revision"] == desired and not item["error"] for item in hosts),
        }

    async def watch(self) -> None:
        while True:
            try:
                await self.reload()
            except Exception as exc:
                self.error = type(exc).__name__
                try:
                    self.acknowledge()
                except OSError:
                    logger.warning("Plugin acknowledgement could not be written")
                logger.warning("Plugin reload failed: %s", self.error)
            await asyncio.sleep(2)

    def close(self) -> None:
        errors = []
        for snapshot in [self.current, *reversed(self.retired)]:
            try:
                self.discard(snapshot)
            except Exception as exc:
                errors.append(exc)
        (self.data_root / ".hosts" / f"{self.host_id}.json").unlink(missing_ok=True)
        self.current = PluginSnapshot()
        self.retired.clear()
        if errors:
            raise ExceptionGroup("Plugin shutdown failed", errors)
