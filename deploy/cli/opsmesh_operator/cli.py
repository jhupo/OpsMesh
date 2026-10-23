from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from importlib.metadata import version
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx

from opsmesh_operator.commands import run_command
from opsmesh_operator.deployments import deployment_for
from opsmesh_operator.installation import Installation
from opsmesh_operator.installer import doctor, install


def send_admin_request(
    api_base_url: str,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
    *,
    archive: Path | None = None,
) -> object:
    """Send an authenticated admin request and close the owned HTTP client.

    Requires HTTPS except for loopback URLs, rejects URL credentials and reads
    the administrator's user Session from the environment. Raises ValueError for unsafe/missing
    configuration and RuntimeError for an unsuccessful HTTP response.
    """
    url = urlsplit(api_base_url)
    if url.username or url.password or url.query or url.fragment:
        raise ValueError("API URL cannot contain credentials, query or fragment")
    if url.scheme != "https" and not (
        url.scheme == "http" and url.hostname in {"127.0.0.1", "::1", "localhost"}
    ):
        raise ValueError("Remote administrative connections require HTTPS")
    session_id = os.environ.get("OPSMESH_SESSION_ID")
    if not session_id:
        raise ValueError("Set OPSMESH_SESSION_ID from an administrator login")
    with httpx.Client(
        base_url=api_base_url, timeout=120, headers={"X-Session-ID": session_id}
    ) as client:
        if archive is not None:
            from opsmesh_sdk.packages import inspect_package

            inspect_package(archive)
            with archive.open("rb") as content:
                response = client.request(
                    method, path, content=content, headers={"Content-Type": "application/zip"}
                )
        else:
            response = client.request(method, path, json=body)
        if response.is_error:
            raise RuntimeError(f"Administrative request failed (HTTP {response.status_code})")
        return response.json()


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the operator CLI's command and safety-flag definitions."""
    root = argparse.ArgumentParser(prog="opsmesh")
    root.add_argument("--root", type=Path, default=Path("/opt/opsmesh"))
    root.add_argument("--api-url", default="http://127.0.0.1:8000")
    root.add_argument("--api-prefix", default="/api/v1")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("version")
    commands.add_parser("doctor")
    monitoring = commands.add_parser("render-alertmanager-config")
    monitoring.add_argument("--output", type=Path)
    host_status = commands.add_parser("status")
    host_status.add_argument("--operation-id", type=UUID)
    release = commands.add_parser("update")
    release.add_argument("--version")
    release.add_argument("--check", action="store_true")
    plugin_root = commands.add_parser("plugin")
    plugin_root.add_argument("--offline", action="store_true")
    plugin = plugin_root.add_subparsers(dest="plugin_action", required=True)
    plugin.add_parser("list")
    plugin.add_parser("reload")
    plugin_install = plugin.add_parser("install")
    plugin_install.add_argument("source")
    plugin_install.add_argument("--sha256")
    for action in ("enable", "disable"):
        command = plugin.add_parser(action)
        command.add_argument("id")
        command.add_argument("--config", type=Path)
    plugin.add_parser("uninstall").add_argument("id")
    rollback = commands.add_parser("rollback")
    rollback.add_argument("id", type=UUID)
    rollback.add_argument("--acknowledge-data-loss", action="store_true")
    logs = commands.add_parser("logs")
    logs.add_argument("--service", choices=["api", "worker", "gateway"], default="api")
    setup = commands.add_parser("install")
    setup.add_argument("--version", required=True)
    setup.add_argument("--origin", required=True)
    setup.add_argument("--mode", choices=["compose", "systemd"], default="systemd")
    setup.add_argument("--repository", default="jhupo/OpsMesh")
    setup.add_argument("--temporal-service-unit")
    admin = commands.add_parser("admin")
    admin_commands = admin.add_subparsers(dest="admin_action", required=True)
    admin_commands.add_parser("reset-password")
    backups = commands.add_parser("backup").add_subparsers(dest="action", required=True)
    backups.add_parser("create")
    verify = backups.add_parser("verify")
    verify.add_argument("id", type=UUID)
    verify.add_argument("--restore-check", action="store_true")
    restore = backups.add_parser("restore")
    restore.add_argument("id", type=UUID)
    restore.add_argument("--acknowledge-data-loss", action="store_true")
    return root


def execute(args: argparse.Namespace) -> object:
    if args.command == "version":
        return {"version": version("opsmesh-operator")}
    if args.command == "doctor":
        return doctor(args.root)
    if args.command == "render-alertmanager-config":
        from opsmesh_operator.observability import render_alertmanager

        return render_alertmanager(args.output or args.root / "alertmanager.generated.yml")
    if args.command == "install":
        credentials = install(
            Installation(
                root=args.root,
                mode=args.mode,
                repository=args.repository,
                temporal_service_unit=args.temporal_service_unit,
            ),
            args.version,
            args.origin,
        )
        return {"installed": True, "tag": args.version, "administrator": credentials}
    if args.command == "admin":
        if os.name != "posix" or os.geteuid() != 0:
            raise ValueError("Administrator password reset requires root")
        installation = Installation.load(args.root)
        if args.admin_action == "reset-password":
            return json.loads(deployment_for(installation).reset_admin_password())
    if args.command == "status":
        if args.operation_id:
            from opsmesh_operator.releases_apply import read_operation

            return read_operation(args.root, str(args.operation_id))
        return send_admin_request(args.api_url, "GET", f"{args.api_prefix}/platform/system/version")
    if args.command == "plugin":
        if hasattr(args, "id") and not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", args.id):
            raise ValueError("Invalid plugin ID")
        if not args.offline:
            endpoint = f"{args.api_prefix}/plugins"
            if args.plugin_action == "list":
                return send_admin_request(args.api_url, "GET", endpoint)
            if args.plugin_action == "reload":
                return send_admin_request(args.api_url, "POST", endpoint + "/reload")
            if args.plugin_action == "install":
                if args.source.startswith("https://"):
                    if not args.sha256:
                        raise ValueError("Online plugin downloads require --sha256")
                    return send_admin_request(
                        args.api_url,
                        "POST",
                        endpoint + "/install-url",
                        {
                            "url": args.source,
                            "sha256": args.sha256,
                        },
                    )
                archive = Path(args.source)
                if args.sha256:
                    with archive.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    if digest != args.sha256.lower():
                        raise ValueError("Plugin checksum mismatch")
                return send_admin_request(
                    args.api_url, "POST", endpoint + "/install", archive=archive
                )
            if args.plugin_action == "uninstall":
                return send_admin_request(
                    args.api_url, "DELETE", endpoint + "/" + quote(args.id, safe="")
                )
            body: dict[str, object] = {"enabled": args.plugin_action == "enable"}
            if args.config is not None:
                body["config"] = json.loads(args.config.read_bytes())
            return send_admin_request(
                args.api_url, "PATCH", endpoint + "/" + quote(args.id, safe=""), body
            )
        if args.plugin_action == "reload":
            raise ValueError("Reload requires a running server; omit --offline")
        from opsmesh_operator.plugins import (
            configure_plugin,
            install_plugin,
            registry,
            uninstall_plugin,
        )

        installation = Installation.load(args.root)
        if args.plugin_action == "list":
            return [
                {"id": key, "version": value["manifest"]["version"], "enabled": value["enabled"]}
                for key, value in registry(args.root).items()
            ]
        if args.plugin_action == "install":
            return install_plugin(installation, args.source, args.sha256)
        if args.plugin_action == "uninstall":
            return uninstall_plugin(installation, args.id)
        return configure_plugin(
            installation, args.id, enabled=args.plugin_action == "enable", config=args.config
        )
    if args.command == "update":
        from opsmesh_operator.releases_apply import apply_release
        from opsmesh_operator.updates import check_update

        installation = Installation.load(args.root)
        result = check_update(installation, args.version)
        if args.check or not result["update_available"]:
            return result
        return apply_release(installation, tag=str(result["available"]), update_operator=True)
    if args.command == "rollback":
        from opsmesh_operator.releases_apply import rollback_release

        return rollback_release(
            Installation.load(args.root),
            operation_id=str(args.id),
            acknowledge_data_loss=args.acknowledge_data_loss,
        )
    if args.command == "logs":
        if Installation.load(args.root).mode == "systemd":
            return run_command(
                ["journalctl", "-u", f"opsmesh-{args.service}", "-n", "100", "--no-pager"]
            )
        from opsmesh_operator.deployments import ComposeDeployment

        backend = ComposeDeployment(Installation.load(args.root))
        return run_command(
            backend.command(args.root / "current")
            + ["logs", "--tail", "100", "--no-color", args.service]
        )
    if args.command == "backup":
        from opsmesh_operator.backups import BackupStore, database_revision
        from opsmesh_operator.maintenance import maintenance_lock

        installation = Installation.load(args.root)
        store = BackupStore(installation)
        if args.action == "verify":
            return store.verify(str(args.id), restore_check=args.restore_check)
        if os.name != "posix" or os.geteuid() != 0:
            raise ValueError("Backup creation and restoration require root on the host")
        if args.action == "restore" and not args.acknowledge_data_loss:
            raise ValueError("Restore requires --acknowledge-data-loss")
        deployment = deployment_for(installation)
        from opsmesh_operator.temporal_backup import TemporalBackup

        TemporalBackup(installation).validate()
        from opsmesh_operator.contracts import ReleaseManifest

        manifest = ReleaseManifest.model_validate_json(
            (installation.root / "current/release-manifest.json").read_bytes()
        )
        with maintenance_lock(installation.root):
            if args.action == "create":
                deployment.stop()
                try:
                    backup_id = store.create(database_revision=database_revision(installation))
                finally:
                    deployment.start(manifest)
                    deployment.healthy()
                return {"id": backup_id}
            if args.action == "restore":
                metadata = store.verify(str(args.id))
                if not manifest.accepts_database_revision(str(metadata["database_revision"])):
                    raise ValueError("The installed release cannot run against the backup schema")
                deployment.stop()
                store.restore(str(args.id), acknowledge_data_loss=True)
                deployment.start(manifest)
                deployment.healthy()
                return {"restored": str(args.id)}
        raise ValueError("Unsupported backup command")
    raise ValueError("Unsupported command")


def main() -> None:
    arguments = build_argument_parser()
    args = arguments.parse_args()
    try:
        payload = json.dumps(execute(args), ensure_ascii=False, indent=2)
        # Install/reset credentials are handed directly to the invoking terminal, never a logger.
        os.write(1, f"{payload}\n".encode())
    except (ValueError, RuntimeError) as exc:
        arguments.exit(1, f"opsmesh: {exc}\n")
    except (OSError, httpx.HTTPError):
        arguments.exit(
            1, "opsmesh: operation unavailable; inspect host configuration/connectivity\n"
        )


if __name__ == "__main__":
    main()
