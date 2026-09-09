from __future__ import annotations

import json
import os
import platform
import secrets
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from filelock import FileLock

from opsmesh_operator.commands import run_command
from opsmesh_operator.deployments import ComposeDeployment, deployment_for
from opsmesh_operator.files import atomic_write, require_install_root, sync_directory
from opsmesh_operator.installation import Installation
from opsmesh_operator.releases import ReleaseSource


def doctor(root: Path) -> dict[str, object]:
    return {
        "platform": platform.system(),
        "architecture": platform.machine(),
        "installed": (root / "installation.json").is_file(),
        "commands": {
            name: shutil.which(name) is not None
            for name in ("docker", "gh", "systemctl", "pg_dump", "pg_restore")
        },
    }


def install(installation: Installation, tag: str, origin: str) -> None:
    if platform.system() != "Linux" or os.geteuid() != 0:
        raise ValueError("Host installation requires a Linux administrator")
    root = require_install_root(installation.root)
    if any(character.isspace() for character in str(root)):
        raise ValueError("Systemd installation paths cannot contain whitespace")
    if urlsplit(origin).scheme != "https" or not urlsplit(origin).hostname:
        raise ValueError("A public HTTPS origin is required; terminate TLS at your reverse proxy")
    root.mkdir(parents=True, exist_ok=True, mode=0o750)
    with FileLock(root / "operator.lock", timeout=0):
        status_path = root / "installation-status.json"
        if status_path.exists():
            status = json.loads(status_path.read_text("utf-8"))
            if (
                status["phase"] == "ready"
                or status["tag"] != tag
                or status["mode"] != installation.mode
            ):
                raise ValueError(
                    "Already installed or different install intent; use update plan/apply"
                )
        elif (root / "installation.json").exists() or (root / "current").exists():
            raise ValueError("Existing installation cannot be overwritten")
        else:
            atomic_write(
                status_path,
                json.dumps({"phase": "installing", "tag": tag, "mode": installation.mode}),
            )
        for name in ("gh", "systemctl", "pg_dump", "pg_restore"):
            if shutil.which(name) is None:
                raise ValueError(f"Install prerequisite {name} before continuing")
        _service_accounts(installation)
        manifest = ReleaseSource(installation.repository).fetch_manifest(
            tag, root / "downloads" / tag
        )
        deployment = deployment_for(installation)
        deployment.preflight(manifest)
        if not (root / ".env").exists():
            password = secrets.token_hex(32)
            values = {
                "OPSMESH_ROOT": str(root),
                "OPSMESH_ENVIRONMENT": "production",
                "OPSMESH_ENABLE_API_DOCS": "false",
                "OPSMESH_API_RATE_LIMIT_ENABLED": "true",
                "OPSMESH_READINESS_WORKER_CHECK_ENABLED": "true",
                "OPSMESH_CORS_ORIGINS": json.dumps([origin]),
                "OPSMESH_POSTGRES_PASSWORD": password,
                "OPSMESH_DATABASE_URL": f"postgresql+psycopg://opsmesh:{password}@127.0.0.1:5432/opsmesh",
                "OPSMESH_REDIS_URL": "redis://127.0.0.1:6379/0",
                "OPSMESH_STORAGE_ROOT": str(root / "data/storage"),
                "OPSMESH_RELEASE_UPDATE_ENABLED": "true",
                "OPSMESH_TRACING_ENABLED": "false",
                "OPSMESH_OTEL_LOGS_ENABLED": "false",
                "OPSMESH_DOCKER_GID": str(Path("/var/run/docker.sock").stat().st_gid),
            }
            for key in (
                "INTERNAL_API_TOKEN",
                "PLATFORM_ADMIN_TOKEN",
                "TOKEN_HASH_PEPPER",
                "CREDENTIAL_ENCRYPTION_SECRET",
                "WORKER_HEARTBEAT_TOKEN",
            ):
                values[f"OPSMESH_{key}"] = secrets.token_hex(32)
            atomic_write(
                root / ".env", "".join(f"{key}={value}\n" for key, value in values.items())
            )
        storage = root / "data/storage"
        storage.mkdir(parents=True, exist_ok=True)
        os.chown(storage, 10001, 10001)
        storage.chmod(0o2770)
        directory = deployment.stage(manifest)
        if isinstance(deployment, ComposeDeployment):
            deployment.infrastructure(manifest)
        # Bootstrap the independently managed updater once. Application updates do not replace
        # this process's environment while it is executing; unsupported protocol changes fail.
        updater_directory = root / "updater"
        provision_updater(directory, updater_directory)
        deployment.migrate(manifest)
        deployment.switch(manifest)
        if installation.mode == "systemd":
            run_command(["chown", "root:opsmesh", str(root), str(root / ".env")])
            (root / ".env").chmod(0o640)
            for name in ("opsmesh-api", "opsmesh-worker"):
                template = (directory / f"deploy/server/systemd/{name}.service").read_text()
                atomic_write(
                    Path(f"/etc/systemd/system/{name}.service"),
                    template.replace("/opt/opsmesh", str(root)),
                    mode=0o644,
                )
        unit = (
            "[Unit]\nDescription=OpsMesh host updater\nAfter=network-online.target\n"
            "[Service]\nType=simple\nUser=root\n"
            f"WorkingDirectory={root}\nEnvironmentFile={root}/.env\n"
            f"EnvironmentFile=-{root}/updater.env\n"
            f"ExecStart={updater_directory}/opsmesh-server updater "
            f"--root {root}\n"
            "Restart=on-failure\nRestartSec=10\nUMask=0077\nPrivateTmp=true\n"
            "[Install]\nWantedBy=multi-user.target\n"
        )
        atomic_write(Path("/etc/systemd/system/opsmesh-updater.service"), unit, mode=0o644)
        installation.save()
        run_command(["systemctl", "daemon-reload"])
        deployment.start(manifest)
        deployment.healthy()
        if installation.mode == "systemd":
            run_command(["systemctl", "enable", "opsmesh-api", "opsmesh-worker"])
        run_command(["systemctl", "enable", "--now", "opsmesh-updater.service"])
        atomic_write(
            status_path, json.dumps({"phase": "ready", "tag": tag, "mode": installation.mode})
        )


def provision_updater(directory: Path, target: Path) -> None:
    """Copy the verified runtime once; application switches never replace the running updater."""
    if target.exists():
        if (target / "BUILD.json").read_bytes() != (directory / "BUILD.json").read_bytes():
            raise ValueError("Independent updater already belongs to a different release")
        return
    staging = target.with_name(".updater.staging")
    if staging.exists():
        raise ValueError("Interrupted updater staging requires operator inspection")
    shutil.copytree(directory, staging, ignore=shutil.ignore_patterns(".env", "__pycache__"))
    staging.rename(target)
    sync_directory(target.parent)


def _service_accounts(installation: Installation) -> None:
    if installation.mode != "systemd":
        return
    import grp
    import pwd

    try:
        group = grp.getgrnam("opsmesh")
        if group.gr_gid != 10001:
            raise ValueError("Service group opsmesh must use the reserved GID 10001")
    except KeyError:
        run_command(["groupadd", "--system", "--gid", "10001", "opsmesh"])
    for name, uid in (("opsmesh-api", 10001), ("opsmesh-worker", 10002)):
        try:
            user = pwd.getpwnam(name)
            if user.pw_uid != uid or user.pw_gid != 10001:
                raise ValueError("Service account identity does not match installation policy")
        except KeyError:
            run_command(
                [
                    "useradd",
                    "--system",
                    "--uid",
                    str(uid),
                    "--gid",
                    "opsmesh",
                    "--no-create-home",
                    "--shell",
                    "/usr/sbin/nologin",
                    name,
                ]
            )
    run_command(["usermod", "--append", "--groups", "docker", "opsmesh-worker"])
