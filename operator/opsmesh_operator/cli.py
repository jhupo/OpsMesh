from __future__ import annotations

import argparse
import json
import os
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

from opsmesh_operator.commands import run_command
from opsmesh_operator.deployments import deployment_for
from opsmesh_operator.installation import Installation
from opsmesh_operator.installer import doctor, install


def send_admin_request(
    api_base_url: str, method: str, path: str, body: dict[str, object] | None = None
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
        base_url=api_base_url, timeout=30, headers={"X-Session-ID": session_id}
    ) as client:
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
    host_status = commands.add_parser("status")
    host_status.add_argument("--operation-id", type=UUID)
    release = commands.add_parser("deploy")
    release.add_argument("--version", required=True)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("id", type=UUID)
    rollback.add_argument("--acknowledge-data-loss", action="store_true")
    logs = commands.add_parser("logs")
    logs.add_argument("--service", choices=["api", "worker"], default="api")
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
    if args.command == "deploy":
        from opsmesh_operator.releases_apply import apply_release

        return apply_release(
            Installation.load(args.root),
            tag=args.version,
            request=lambda method, path, body: send_admin_request(
                args.api_url, method, f"{args.api_prefix}{path}", body
            ),
        )
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
        from opsmesh_operator.releases_apply import change_admission

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
                operation_id = str(uuid4())

                def admin_request(method: str, path: str, body: dict[str, object] | None) -> object:
                    return send_admin_request(
                        args.api_url, method, f"{args.api_prefix}{path}", body
                    )

                change_admission(
                    admin_request,
                    drained=True,
                    operation_id=operation_id,
                    timeout=installation.timeout_seconds,
                )
                deployment.stop()
                try:
                    backup_id = store.create(database_revision=database_revision(installation))
                finally:
                    deployment.start(manifest)
                    deployment.healthy()
                change_admission(
                    admin_request,
                    drained=False,
                    operation_id=operation_id,
                    timeout=installation.timeout_seconds,
                )
                return {"id": backup_id}
            if args.action == "restore":
                metadata = store.verify(str(args.id))
                if not manifest.accepts_database_revision(str(metadata["database_revision"])):
                    raise ValueError("The installed release cannot run against the backup schema")
                deployment.stop()
                store.restore(str(args.id), acknowledge_data_loss=True)
                deployment.start(manifest)
                deployment.healthy()
                return {"restored": str(args.id), "admission": "retained from backup"}
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
