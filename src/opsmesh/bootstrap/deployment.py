"""Linux installation and opt-in updates, separate from the application workers."""

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy import func, select

from opsmesh.infra.database import connect, now
from opsmesh.infra.encryption import SecretCipher
from opsmesh.sessions.models import AgentSession
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.tasks.models import Task

from .releases import (
    copy_bundle,
    download_release,
    latest_release,
    linux_platform,
    manifest,
    release_client,
    repository_name,
)
from .schema import load_models
from .settings import Settings

PREFIX = Path("/opt/opsmesh")
CONFIG = Path("/etc/opsmesh")
DATA = Path("/var/lib/opsmesh")
LOGS = Path("/var/log/opsmesh")
UNITS = Path("/etc/systemd/system")
SERVICES = ("opsmesh-api", "opsmesh-tasks", "opsmesh-scheduler", "opsmesh-gateway")
INGRESS = ("opsmesh-api", "opsmesh-scheduler", "opsmesh-gateway")


def configure_parser(commands: Any) -> None:
    install = commands.add_parser(
        "install", help="Install an extracted Linux release as systemd services"
    )
    install.add_argument("--config", type=Path, required=True, help="Prepared .env file")
    install.add_argument("--repository", required=True, help="Public GitHub owner/repository")
    install.add_argument("--health-url", default="http://127.0.0.1:8000/health")
    install.add_argument("--auto-update", action="store_true")
    install.add_argument("--start", action="store_true")
    update = commands.add_parser("update", help="Check or apply the latest stable release")
    update.add_argument("--check", action="store_true", help="Only report available updates")
    commands.add_parser(
        "upgrade-ready", help="Reject upgrades while tasks are queued, running or awaiting input"
    )


def run(
    *arguments: str, check: bool = True, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=check, text=True, cwd=cwd)


def systemctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run("systemctl", *arguments, check=check)


def require_root() -> None:
    linux_platform()
    if os.geteuid() != 0:
        raise InvalidArgumentError("Installation and updates require root")


@contextlib.contextmanager
def deployment_lock() -> Iterator[None]:
    import fcntl

    with Path("/run/lock/opsmesh-deployment.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConflictError("Another installation or update is running") from exc
        yield


def validate_settings(config: Path) -> None:
    try:
        settings = Settings(_env_file=config)
        SecretCipher(settings.encryption_key.get_secret_value())
    except (ValidationError, ValueError) as exc:
        raise InvalidArgumentError(
            "Configure a valid PostgreSQL URL and encryption key before installation"
        ) from exc
    if not settings.database_url.get_secret_value().startswith("postgresql+psycopg://"):
        raise InvalidArgumentError("Deployment requires a PostgreSQL psycopg URL")
    if settings.data_dir != DATA or settings.log_dir != LOGS:
        raise InvalidArgumentError(
            "Set OPSMESH_DATA_DIR=/var/lib/opsmesh and OPSMESH_LOG_DIR=/var/log/opsmesh"
        )


def activate(bundle: Path) -> None:
    temporary = PREFIX / ".current-next"
    if temporary.exists() or temporary.is_symlink():
        raise ConflictError("An unfinished activation exists; inspect /opt/opsmesh/.current-next")
    temporary.symlink_to(bundle, target_is_directory=True)
    temporary.replace(PREFIX / "current")


def install_units(bundle: Path) -> None:
    for source in sorted((bundle / "systemd").iterdir()):
        if source.is_file() and source.suffix in {".service", ".target", ".timer"}:
            shutil.copyfile(source, UNITS / source.name)
            (UNITS / source.name).chmod(0o644)
    systemctl("daemon-reload")


def install(arguments: argparse.Namespace) -> None:
    require_root()
    if not getattr(sys, "frozen", False):
        raise InvalidArgumentError("Install from the published Linux executable bundle")
    repository = repository_name(arguments.repository)
    validate_settings(arguments.config.resolve())
    health_url = httpx.URL(arguments.health_url)
    if health_url.scheme != "http" or health_url.host not in {"127.0.0.1", "::1"}:
        raise InvalidArgumentError("Health URL must use HTTP on a loopback address")
    source = Path(sys.executable).resolve().parent.parent
    manifest(source)
    if (PREFIX / "current").exists() or (CONFIG / ".env").exists():
        raise ConflictError("OpsMesh is already installed; use update instead")
    with deployment_lock():
        import grp
        import pwd

        for directory in (PREFIX, CONFIG, DATA, LOGS):
            if directory.is_symlink() or (
                directory.exists() and (not directory.is_dir() or any(directory.iterdir()))
            ):
                raise ConflictError(f"Installation directory is already in use: {directory}")
        for source_unit in (source / "systemd").iterdir():
            target_unit = UNITS / source_unit.name
            if target_unit.exists() or target_unit.is_symlink():
                raise ConflictError(f"A service unit already exists: {target_unit}")
        try:
            pwd.getpwnam("opsmesh")
        except KeyError:
            run(
                "useradd",
                "--system",
                "--user-group",
                "--home-dir",
                str(DATA),
                "--shell",
                "/usr/sbin/nologin",
                "opsmesh",
            )
        account = pwd.getpwnam("opsmesh")
        group = grp.getgrnam("opsmesh")
        for directory in (PREFIX, PREFIX / "releases", CONFIG, DATA, LOGS):
            directory.mkdir(parents=True, exist_ok=True)
        for directory in (DATA, LOGS):
            os.chown(directory, account.pw_uid, group.gr_gid)
            directory.chmod(0o750)
        os.chown(CONFIG, 0, group.gr_gid)
        CONFIG.chmod(0o750)
        with open(
            CONFIG / ".env", "x", opener=lambda path, flags: os.open(path, flags, 0o640)
        ) as target:
            target.write(arguments.config.read_text())
        os.chown(CONFIG / ".env", 0, group.gr_gid)
        (CONFIG / ".env").chmod(0o640)
        (CONFIG / "update.json").write_text(
            json.dumps({"repository": repository, "health_url": str(health_url)}, indent=2) + "\n"
        )
        (CONFIG / "update.json").chmod(0o600)
        bundle = copy_bundle(source, PREFIX / "releases")
        activate(bundle)
        install_units(bundle)
        systemctl("enable", "opsmesh.target")
        if arguments.start:
            systemctl("start", "opsmesh.target")
            wait_healthy(str(health_url))
        if arguments.auto_update:
            systemctl("enable", "--now", "opsmesh-update.timer")
    print("Installed OpsMesh. Configuration: /etc/opsmesh/.env; services: opsmesh.target")


def upgrade_ready() -> None:
    settings = Settings()
    load_models()
    sessions = connect(settings.database_url.get_secret_value())
    try:
        with sessions() as db:
            pending = db.scalar(
                select(func.count())
                .select_from(Task)
                .where(Task.status.in_(["queued", "running", "waiting_input"]))
            )
            if pending:
                raise ConflictError(
                    "Finish or cancel queued, running and waiting-input tasks before updating",
                    code="update_busy",
                    details={"active_tasks": pending},
                )
            accesses = db.scalar(
                select(func.count())
                .select_from(AgentSession)
                .where(AgentSession.sandbox_access_until > now())
            )
            if accesses:
                raise ConflictError(
                    "Sandbox file operations are still active",
                    code="update_busy",
                    details={"active_sessions": accesses},
                )
    finally:
        sessions.kw["bind"].dispose()


def wait_healthy(url: str) -> None:
    with httpx.Client(timeout=3, trust_env=False) as client:
        for _ in range(30):
            active = all(
                systemctl("is-active", "--quiet", service, check=False).returncode == 0
                for service in SERVICES
            )
            if active:
                try:
                    response = client.get(url)
                    if response.status_code == 200 and response.json() == {"status": "alive"}:
                        return
                except (httpx.HTTPError, ValueError):
                    pass
            time.sleep(1)
    raise ConflictError(
        "New services did not become healthy; inspect journalctl -u 'opsmesh-*'. No database rollback was attempted"
    )


def update(*, check: bool = False) -> None:
    require_root()
    with deployment_lock():
        configuration = json.loads((CONFIG / "update.json").read_text())
        repository = repository_name(configuration["repository"])
        current = (PREFIX / "current").resolve(strict=True)
        installed = manifest(current)
        with release_client() as client:
            release = latest_release(client, repository)
            available = release["tag_name"][1:]
            if tuple(map(int, available.split("."))) <= tuple(
                map(int, installed["version"].split("."))
            ):
                print(f"OpsMesh {installed['version']} is current")
                return
            print(f"Available: {installed['version']} -> {available}", flush=True)
            if check:
                return
            if systemctl("is-active", "--quiet", "opsmesh.target", check=False).returncode:
                raise ConflictError("Start opsmesh.target before applying updates")
            with tempfile.TemporaryDirectory(prefix="opsmesh-release-") as folder:
                bundle = download_release(client, repository, release, Path(folder))
                prepared = False
                try:
                    systemctl("stop", *INGRESS)
                    ready = run(
                        "runuser",
                        "-u",
                        "opsmesh",
                        "--",
                        str(current / "bin/opsmesh"),
                        "upgrade-ready",
                        check=False,
                        cwd=CONFIG,
                    )
                    if ready.returncode == 3:
                        print(
                            "Update deferred: tasks or sandbox file operations are active",
                            flush=True,
                        )
                        return
                    ready.check_returncode()
                    destination = copy_bundle(bundle, PREFIX / "releases")
                    prepared = True
                finally:
                    if not prepared:
                        systemctl("start", *INGRESS)
                systemctl("stop", "opsmesh.target")
                activate(destination)
                install_units(destination)
                # New code and migrations stay together on failure. Old releases and user data remain.
                systemctl("start", "opsmesh.target")
                wait_healthy(configuration["health_url"])
                print(f"Updated OpsMesh to {available}", flush=True)


def manage_deployment(arguments: argparse.Namespace) -> None:
    if arguments.command == "install":
        install(arguments)
    elif arguments.command == "upgrade-ready":
        upgrade_ready()
    else:
        update(check=arguments.check)
