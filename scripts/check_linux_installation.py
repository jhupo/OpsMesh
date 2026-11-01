"""Exercise a real, isolated Linux installation on a fresh CI runner.

Run explicitly as root with --mode local or --mode docker. This check leaves its
account, installation directories and Docker volumes for inspection; it stops
only services belonging to the instance it created.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTS = {"local": 18941, "docker": 18942}
SENSITIVE_VALUES: set[str] = set()
CONFIGURATION_FILES: set[Path] = set()


def safe_diagnostic(text: str) -> str:
    """Redact bootstrap, provider and generated database credentials before logging."""
    sensitive_name = re.compile(r"PASSWORD|SECRET|TOKEN|API_KEY|DATABASE_URL", re.IGNORECASE)
    for name, value in os.environ.items():
        if value and sensitive_name.search(name):
            SENSITIVE_VALUES.add(value)
    for path in CONFIGURATION_FILES:
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and not name.startswith("#") and sensitive_name.search(name):
                value = value.strip().strip("\"'")
                if value:
                    SENSITIVE_VALUES.add(value)
    for value in sorted(SENSITIVE_VALUES, key=len, reverse=True):
        text = text.replace(value, "[redacted]")
    return re.sub(r"://[^/\s:@]+:[^@\s]+@", "://[redacted]@", text)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def command(arguments: list[str], *, timeout: float = 120, check: bool = True) -> str:
    """Capture output so installer errors cannot echo credentials into CI logs."""
    label = " ".join(arguments[:2])
    try:
        result = subprocess.run(
            arguments,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{label} exceeded its {timeout:g}-second timeout") from None
    if check and result.returncode:
        diagnostic = safe_diagnostic(result.stdout + "\n" + result.stderr)[-4000:]
        raise RuntimeError(f"{label} exited with status {result.returncode}\n{diagnostic}")
    return result.stdout.strip()


class InstallationCheck:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.instance = f"opsmesh-ci-{mode}"
        self.port = PORTS[mode]
        self.service = f"{self.instance}.service"
        self.installation = Path("/opt") / self.instance
        self.configuration = Path("/etc") / self.instance
        self.environment = self.configuration / "opsmesh.env"
        self.current = self.installation / "current"
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.password = secrets.token_urlsafe(36)
        SENSITIVE_VALUES.add(self.password)
        CONFIGURATION_FILES.add(self.environment)

    def compose(self, *arguments: str, timeout: float = 120) -> str:
        source = self.current if self.current.exists() else ROOT
        return command(
            [
                "docker",
                "compose",
                "--project-name",
                self.instance,
                "--env-file",
                str(self.environment),
                "--file",
                str(source / "deploy" / "compose.yaml"),
                *arguments,
            ],
            timeout=timeout,
        )

    def assert_fresh_instance(self) -> None:
        for path in (
            self.installation,
            self.configuration,
            Path("/var/lib") / self.instance,
            Path("/etc/systemd/system") / self.service,
        ):
            require(not path.exists() and not path.is_symlink(), "CI instance already exists")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", self.port))
        if self.mode == "local":
            require(Path("/run/systemd/system").is_dir(), "An active systemd manager is required")
            require(
                not command(["getent", "passwd", self.instance], check=False),
                "CI service user already exists",
            )
            require(
                not command(["getent", "group", self.instance], check=False),
                "CI service group already exists",
            )
        else:
            command(["docker", "info"])
            command(["docker", "compose", "version"])
            for resource in ("container", "volume", "network"):
                arguments = ["docker", resource, "ls", "--quiet"]
                if resource == "container":
                    arguments.append("--all")
                arguments += ["--filter", f"label=com.docker.compose.project={self.instance}"]
                require(not command(arguments), "CI Compose project already has resources")

    def install(self, *arguments: str) -> None:
        command(
            [
                "bash",
                str(ROOT / "deploy" / "install.sh"),
                "--mode",
                self.mode,
                "--instance",
                self.instance,
                "--port",
                str(self.port),
                *arguments,
            ],
            timeout=1200,
        )

    def assert_not_started(self) -> None:
        if self.mode == "local":
            state = command(["systemctl", "is-active", self.service], check=False)
            require(state not in {"active", "activating"}, "--no-start started the native service")
        else:
            running = self.compose("ps", "--status", "running", "--services").splitlines()
            require(not {"api", "worker"}.intersection(running), "--no-start started applications")
            require(
                {"postgres", "redis"}.issubset(running), "Bootstrap dependencies are not running"
            )

    def start(self) -> None:
        if self.mode == "local":
            command(["systemctl", "start", self.service])
        else:
            self.compose("up", "-d", "--wait", "--wait-timeout", "120", timeout=180)
        self.wait_ready()

    def restart(self) -> None:
        if self.mode == "local":
            command(["systemctl", "restart", self.service])
        else:
            self.compose("restart", "api", "worker")
        self.wait_ready()

    def wait_ready(self) -> None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{self.base_url}/ready", timeout=3) as response:
                    if response.status == 200:
                        return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.5)
        raise RuntimeError("Installation did not become ready within 60 seconds")

    def authenticated_principal(self) -> str:
        payload = json.dumps({"username": "admin", "password": self.password}).encode()
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/auth/login",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            login = json.load(response)
        require(
            isinstance(login.get("token"), str) and bool(login["token"]), "Login returned no token"
        )
        SENSITIVE_VALUES.add(login["token"])
        principal_id = login["principal"]["id"]
        require(isinstance(principal_id, str) and bool(principal_id), "Login returned no principal")
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login['token']}"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            principal = json.load(response)
        require(principal["id"] == principal_id, "Authenticated principal is inconsistent")
        return principal_id

    def assert_runtime_credentials(self) -> None:
        configuration = self.environment.read_bytes()
        require(
            self.password.encode() not in configuration, "Bootstrap password persisted in config"
        )
        require(
            re.search(rb"(?m)^\s*(?:export\s+)?OPSMESH_ADMIN_PASSWORD\s*=", configuration) is None,
            "Bootstrap password variable persisted in config",
        )
        if self.mode == "local":
            pid = int(command(["systemctl", "show", self.service, "--property=MainPID", "--value"]))
            require(pid > 1, "Native service has no process")
            process = Path("/proc") / str(pid)
            identity = re.search(r"(?m)^Uid:\s+(.+)$", (process / "status").read_text())
            require(identity is not None, "Native process UID is unavailable")
            require(
                all(int(value) > 0 for value in identity[1].split()), "Native service runs as root"
            )
            environment = (process / "environ").read_bytes()
            argv = (process / "cmdline").read_bytes()
            require(
                self.password.encode() not in environment + argv,
                "Bootstrap password reached service",
            )
            for assignment in environment.split(b"\0"):
                if assignment.startswith(b"OPSMESH_ADMIN_PASSWORD="):
                    require(
                        assignment == b"OPSMESH_ADMIN_PASSWORD=",
                        "Service retained bootstrap password",
                    )
        else:
            for service in ("api", "worker"):
                identifier = self.compose("ps", "--quiet", service)
                require(
                    bool(identifier) and "\n" not in identifier,
                    "Expected one application container",
                )
                configuration = json.loads(command(["docker", "inspect", identifier]))[0]["Config"]
                environment = configuration.get("Env") or []
                require(
                    self.password not in json.dumps(configuration),
                    "Bootstrap password reached application container",
                )
                for assignment in environment:
                    if assignment.startswith("OPSMESH_ADMIN_PASSWORD="):
                        require(
                            assignment == "OPSMESH_ADMIN_PASSWORD=",
                            "Container retained bootstrap password",
                        )
                require(
                    self.compose("exec", "-T", service, "id", "-u") != "0", "Container runs as root"
                )

    def stop_owned_services(self) -> None:
        if self.mode == "local":
            unit = Path("/etc/systemd/system") / self.service
            if unit.is_file():
                command(["systemctl", "disable", "--now", self.service], timeout=150)
        elif self.environment.is_file():
            self.compose("down", "--timeout", "60", timeout=180)

    def run(self) -> None:
        self.assert_fresh_instance()
        try:
            with tempfile.TemporaryDirectory(prefix="opsmesh-install-ci-") as temporary:
                credential = Path(temporary) / "administrator-password"
                with credential.open(
                    "x", opener=lambda path, flags: os.open(path, flags, 0o600)
                ) as stream:
                    stream.write(self.password)
                print(f"Checking {self.mode}: first install with --no-start", flush=True)
                self.install("--admin-password-file", str(credential), "--no-start")
                credential.unlink()
            self.assert_not_started()
            self.start()
            original_principal = self.authenticated_principal()
            self.assert_runtime_credentials()
            with self.environment.open("ab") as stream:
                stream.write(b"\n# CI configuration preservation marker\n")
            original_configuration = hashlib.sha256(self.environment.read_bytes()).digest()
            original_release = self.current.resolve(strict=True)

            print(f"Checking {self.mode}: repeat install without bootstrap credentials", flush=True)
            self.install()
            self.wait_ready()
            require(
                self.current.resolve(strict=True) == original_release,
                "Repeat install replaced identical code",
            )
            require(
                self.authenticated_principal() == original_principal,
                "Repeat install changed administrator",
            )
            require(
                hashlib.sha256(self.environment.read_bytes()).digest() == original_configuration,
                "Repeat install replaced configuration",
            )

            print(f"Checking {self.mode}: explicit upgrade and restart", flush=True)
            self.install("--upgrade")
            require(
                self.current.resolve(strict=True) != original_release,
                "Upgrade did not activate new release",
            )
            self.restart()
            require(
                self.authenticated_principal() == original_principal,
                "Upgrade changed administrator",
            )
            require(
                hashlib.sha256(self.environment.read_bytes()).digest() == original_configuration,
                "Upgrade replaced configuration",
            )
            self.assert_runtime_credentials()
            print(f"Linux {self.mode} installation checks passed.", flush=True)
        finally:
            self.stop_owned_services()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=tuple(PORTS), required=True)
    arguments = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error(
            "This destructive integration check requires Linux and root on a fresh CI runner"
        )
    try:
        InstallationCheck(arguments.mode).run()
    except RuntimeError as error:
        print(safe_diagnostic(f"Linux installation check failed: {error}."), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        # Do not print response bodies, subprocess output or environment values.
        print(f"Linux installation check failed ({type(error).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
