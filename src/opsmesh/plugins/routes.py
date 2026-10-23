"""Authenticated package management shared by the online CLI and file import."""

import asyncio
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from zipfile import BadZipFile

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from opsmesh_sdk import PluginManifest
from opsmesh_sdk.packages import MAX_PACKAGE_SIZE
from pydantic import BaseModel, ConfigDict, Field

from opsmesh.plugins.manager import PluginManager
from opsmesh.shared.errors import InvalidArgumentError, NotFoundError
from opsmesh.shared.http.egress import public_https_response
from opsmesh.users.access import require_platform_admin
from opsmesh.users.auth.dependencies import CurrentUserDep

router = APIRouter(prefix="/plugins", tags=["plugins"])


class PluginPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    config: dict[str, object] | None = None


class PluginURL(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=4000)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    enabled: bool = False


def manager(request: Request) -> PluginManager:
    value = getattr(request.app.state, "plugins", None)
    if not isinstance(value, PluginManager):
        raise HTTPException(status_code=503, detail="Plugin management is unavailable")
    return value


@router.get("", operation_id="plugins.list")
async def list_plugins(request: Request, user: CurrentUserDep) -> dict[str, Any]:
    require_platform_admin(user)
    host = manager(request)
    records = await asyncio.to_thread(host.packages.read)
    return {
        "plugins": [
            {
                "manifest": value["manifest"],
                "enabled": value.get("enabled", False),
                "configured": bool(value.get("encrypted_config") or value.get("config")),
            }
            for value in records.values()
        ],
        **host.status(),
    }


@router.get("/resources", operation_id="plugins.resources")
async def list_resources(request: Request, user: CurrentUserDep) -> dict[str, Any]:
    require_platform_admin(user)
    snapshot = manager(request).current
    return {
        "revision": snapshot.revision,
        "resources": [
            {
                "id": item.id,
                "plugin_id": item.plugin_id,
                "kind": item.kind,
                "name": item.name,
                "metadata": item.metadata,
            }
            for item in snapshot.resources.values()
        ],
    }


async def install_archive(host: PluginManager, archive: Path, enabled: bool) -> dict[str, Any]:
    identity: list[str] = []

    def mutate(records: dict[str, Any]) -> dict[str, Any]:
        record: dict[str, Any] = {}

        def validate(manifest: PluginManifest, directory: Path) -> None:
            record.update(manifest=manifest.model_dump(), enabled=True)
            old = records.get(manifest.id, {})
            for key in ("config", "encrypted_config", "config_key_id"):
                if key in old:
                    record[key] = old[key]
            validation = host.prepare(
                {**records, manifest.id: record}, directories={manifest.id: directory}
            )
            host.discard(validation)

        manifest = host.packages.install(archive, validate=validate)
        record["enabled"] = enabled
        records[manifest.id] = record
        identity.append(manifest.id)
        return records

    try:
        result = await host.change(mutate)
    except (ValueError, TypeError, KeyError, BadZipFile) as exc:
        raise InvalidArgumentError("Plugin installation or resource validation failed") from exc
    return {"id": identity[0], "enabled": enabled, "restart_required": False, **result}


@router.post("/install", status_code=202, operation_id="plugins.install")
async def upload(request: Request, user: CurrentUserDep, enabled: bool = False) -> dict[str, Any]:
    require_platform_admin(user)
    host = manager(request)
    with TemporaryDirectory() as directory:
        archive = Path(directory) / "plugin.zip"
        size = 0
        with archive.open("wb") as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_PACKAGE_SIZE:
                    raise HTTPException(status_code=413, detail="Plugin ZIP exceeds 50 MB")
                target.write(chunk)
        return await install_archive(host, archive, enabled)


@router.post("/install-url", status_code=202, operation_id="plugins.install_url")
async def install_url(body: PluginURL, request: Request, user: CurrentUserDep) -> dict[str, Any]:
    require_platform_admin(user)
    with TemporaryDirectory() as directory:
        archive = Path(directory) / "plugin.zip"
        digest, size = hashlib.sha256(), 0

        try:
            async with public_https_response(body.url) as response:
                with archive.open("wb") as target:
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > MAX_PACKAGE_SIZE:
                            raise HTTPException(status_code=413, detail="Plugin ZIP exceeds 50 MB")
                        digest.update(chunk)
                        target.write(chunk)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise InvalidArgumentError("Plugin download failed") from exc
        if digest.hexdigest() != body.sha256.lower():
            raise InvalidArgumentError("Plugin checksum mismatch")
        return await install_archive(manager(request), archive, body.enabled)


@router.post("/reload", status_code=202, operation_id="plugins.reload")
async def reload_plugins(request: Request, user: CurrentUserDep) -> dict[str, Any]:
    require_platform_admin(user)
    host = manager(request)
    await host.reload()
    return host.status()


@router.patch("/{plugin_id}", status_code=202, operation_id="plugins.configure")
async def configure(
    plugin_id: str,
    body: PluginPatch,
    request: Request,
    user: CurrentUserDep,
) -> dict[str, Any]:
    require_platform_admin(user)
    host = manager(request)

    def mutate(records: dict[str, Any]) -> dict[str, Any]:
        if plugin_id not in records:
            raise NotFoundError("Plugin is not installed")
        record = records[plugin_id]
        if body.enabled is not None:
            record["enabled"] = body.enabled
        if body.config is not None:
            encrypted = host.cipher.encrypt_payload(body.config)
            record.pop("config", None)
            record["encrypted_config"] = encrypted.ciphertext
            record["config_key_id"] = encrypted.key_id
        return records

    try:
        return await host.change(mutate)
    except (ValueError, TypeError) as exc:
        raise InvalidArgumentError("Plugin configuration or resource validation failed") from exc


@router.delete("/{plugin_id}", status_code=202, operation_id="plugins.uninstall")
async def uninstall(plugin_id: str, request: Request, user: CurrentUserDep) -> dict[str, Any]:
    require_platform_admin(user)

    def mutate(records: dict[str, Any]) -> dict[str, Any]:
        if plugin_id not in records:
            raise NotFoundError("Plugin is not installed")
        records.pop(plugin_id)
        return records

    return {"files_retained": True, **await manager(request).change(mutate)}
