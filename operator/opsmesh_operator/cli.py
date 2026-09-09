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
from opsmesh_operator.installation import Installation
from opsmesh_operator.installer import doctor, install


def request(base: str, method: str, path: str, body: dict[str, object] | None = None) -> object:
    url = urlsplit(base)
    if url.username or url.password or url.query or url.fragment:
        raise ValueError("API URL cannot contain credentials, query or fragment")
    if url.scheme != "https" and not (
        url.scheme == "http" and url.hostname in {"127.0.0.1", "::1", "localhost"}
    ):
        raise ValueError("Remote administrative connections require HTTPS")
    token = os.environ.get("OPSMESH_PLATFORM_ADMIN_TOKEN")
    if not token:
        raise ValueError("Set OPSMESH_PLATFORM_ADMIN_TOKEN in the environment")
    with httpx.Client(
        base_url=base, timeout=30, headers={"Authorization": f"Bearer {token}"}
    ) as client:
        response = client.request(method, path, json=body)
        if response.is_error:
            raise RuntimeError(f"Administrative request failed (HTTP {response.status_code})")
        return response.json()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="opsmesh")
    root.add_argument("--root", type=Path, default=Path("/opt/opsmesh"))
    root.add_argument("--api-url", default="http://127.0.0.1:8000")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("version")
    commands.add_parser("doctor")
    commands.add_parser("status")
    logs = commands.add_parser("logs")
    logs.add_argument("--service", choices=["api", "worker", "updater"], default="updater")
    setup = commands.add_parser("install")
    setup.add_argument("--version", required=True)
    setup.add_argument("--origin", required=True)
    setup.add_argument("--mode", choices=["compose", "systemd"], default="compose")
    updates = commands.add_parser("update").add_subparsers(dest="action", required=True)
    updates.add_parser("check")
    for name in ("plan", "rollback"):
        plan = updates.add_parser(name)
        plan.add_argument("--version", required=True)
        plan.add_argument("--idempotency-key", default=None)
    apply = updates.add_parser("apply")
    apply.add_argument("--plan", type=UUID, required=True)
    apply.add_argument("--fingerprint", required=True)
    status = updates.add_parser("status")
    status.add_argument("plan", type=UUID)
    status.add_argument("--local", action="store_true")
    cancel = updates.add_parser("cancel")
    cancel.add_argument("--plan", type=UUID, required=True)
    recover = updates.add_parser("recover")
    recover.add_argument("--plan", type=UUID, required=True)
    recover.add_argument("--strategy", choices=["resume", "rollback", "restore"], required=True)
    recover.add_argument("--ack-data-loss", action="store_true")
    backups = commands.add_parser("backup").add_subparsers(dest="action", required=True)
    backups.add_parser("create")
    verify = backups.add_parser("verify")
    verify.add_argument("id", type=UUID)
    verify.add_argument("--restore-check", action="store_true")
    return root


def execute(args: argparse.Namespace) -> object:
    if args.command == "version":
        return {"version": version("opsmesh-operator")}
    if args.command == "doctor":
        return doctor(args.root)
    if args.command == "install":
        install(Installation(root=args.root, mode=args.mode), args.version, args.origin)
        return {"installed": True, "tag": args.version}
    if args.command == "status":
        return request(args.api_url, "GET", "/api/v1/admin/system/version")
    if args.command == "logs":
        if args.service == "updater" or Installation.load(args.root).mode == "systemd":
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
        if args.action == "verify":
            from opsmesh_operator.backups import BackupStore

            return BackupStore(Installation.load(args.root)).verify(
                str(args.id), restore_check=args.restore_check
            )
        current = request(args.api_url, "GET", "/api/v1/admin/system/version")
        if not isinstance(current, dict):
            raise ValueError("Invalid current release response")
        return request(
            args.api_url,
            "POST",
            "/api/v1/admin/system/updates/plans",
            {"tag": current["tag"], "action": "backup", "idempotency_key": uuid4().hex},
        )
    if args.action == "check":
        return request(args.api_url, "GET", "/api/v1/admin/system/check-updates")
    if args.action in {"plan", "rollback"}:
        return request(
            args.api_url,
            "POST",
            "/api/v1/admin/system/updates/plans",
            {
                "tag": args.version,
                "action": "rollback" if args.action == "rollback" else "update",
                "idempotency_key": args.idempotency_key or uuid4().hex,
            },
        )
    if args.action == "status":
        if args.local:
            return json.loads((args.root / "updates" / f"{args.plan}.json").read_text("utf-8"))
        return request(args.api_url, "GET", f"/api/v1/admin/system/updates/{args.plan}")
    if args.action == "recover":
        installation = Installation.load(args.root)
        if args.strategy == "restore" and not args.ack_data_loss:
            raise ValueError("Database restoration requires --ack-data-loss")
        command = [
            str(installation.root / "updater/bin/python"),
            "-m",
            "backend.app.admin.updates.daemon",
            "--root",
            str(installation.root),
            "--recover",
            str(args.plan),
            "--strategy",
            args.strategy,
        ]
        if args.ack_data_loss:
            command.append("--ack-data-loss")
        run_command(command, cwd=installation.root, timeout=7200)
        return {"recovered": True}
    body = {"plan_sha256": args.fingerprint} if args.action == "apply" else None
    return request(
        args.api_url, "POST", f"/api/v1/admin/system/updates/{args.plan}/{args.action}", body
    )


def main() -> None:
    arguments = parser()
    args = arguments.parse_args()
    try:
        print(json.dumps(execute(args), ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError) as exc:
        arguments.exit(1, f"opsmesh: {exc}\n")
    except (OSError, httpx.HTTPError):
        arguments.exit(
            1, "opsmesh: operation unavailable; inspect host configuration/connectivity\n"
        )


if __name__ == "__main__":
    main()
