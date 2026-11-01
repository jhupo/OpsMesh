"""Start only a disposable loopback PostgreSQL cluster owned by this test invocation."""

from __future__ import annotations

import csv
import hashlib
import hmac
import os
import secrets
import socket
import subprocess
from base64 import b64encode
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.engine import URL


def _scram_verifier(password: str) -> str:
    """Build PostgreSQL's SCRAM verifier for this fixture's generated ASCII password."""
    salt = secrets.token_bytes(16)
    iterations = 4096
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, iterations)
    client_key = hmac.digest(salted, b"Client Key", "sha256")
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.digest(salted, b"Server Key", "sha256")
    encoded_salt, encoded_stored, encoded_server = (
        b64encode(value).decode("ascii") for value in (salt, stored_key, server_key)
    )
    return f"SCRAM-SHA-256${iterations}:{encoded_salt}${encoded_stored}:{encoded_server}"


@dataclass
class TemporaryPostgreSQL:
    binaries: Path
    port: int = 0
    password: str = field(default_factory=lambda: secrets.token_urlsafe(30), repr=False)
    username: str = "opsmesh_test"
    directory: Path | None = None

    def url(self, database: str = "postgres", *, async_driver: bool = True) -> str:
        return URL.create(
            "postgresql+asyncpg" if async_driver else "postgresql",
            username=self.username,
            password=self.password,
            host="127.0.0.1",
            port=self.port,
            database=database,
        ).render_as_string(hide_password=False)

    def _command(self, executable: str, *arguments: str) -> None:
        binary = self.binaries / (executable + (".exe" if os.name == "nt" else ""))
        assert self.directory is not None
        output_file = self.directory / f"{executable}.log"
        # pg_ctl's detached Windows child may inherit pipes; regular files keep wait bounded.
        with output_file.open("ab") as output:
            result = subprocess.run(
                [str(binary), *arguments],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=45,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        if result.returncode:
            logfile = self.directory / "server.log" if self.directory else None
            log = (
                logfile.read_text(encoding="utf-8", errors="replace")
                if logfile and logfile.exists()
                else ""
            )
            output_text = output_file.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(f"Temporary PostgreSQL {executable} failed: {output_text}\n{log}")

    def __enter__(self) -> TemporaryPostgreSQL:
        self._temporary = TemporaryDirectory(prefix="opsmesh-postgres-")
        self.directory = Path(self._temporary.name).resolve()
        if os.name == "nt":
            # PostgreSQL drops administrator-group privileges; grant only this user's SID
            # access to this newly created test directory, never to a parent or existing tree.
            identity = subprocess.run(
                ["whoami", "/user", "/fo", "csv", "/nh"],
                capture_output=True,
                text=True,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            sid = next(csv.reader([identity.stdout.strip()]))[1]
            subprocess.run(
                ["icacls", str(self.directory), "/grant", f"*{sid}:(OI)(CI)F"],
                capture_output=True,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        self.data = self.directory / "data"
        verifier_file = self.directory / "init.scram"
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        try:
            # initdb passes this value to ALTER ROLE, which preserves SCRAM verifiers.
            # Persist only the salted verifier, never the connection password.
            with os.fdopen(
                os.open(verifier_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                "w",
                encoding="ascii",
            ) as output:
                output.write(_scram_verifier(self.password))
            try:
                self._command(
                    "initdb",
                    "-D",
                    str(self.data),
                    "--username",
                    self.username,
                    "--pwfile",
                    str(verifier_file),
                    "--auth=scram-sha-256",
                    "--no-locale",
                    "--encoding=UTF8",
                )
            finally:
                verifier_file.unlink(missing_ok=True)
            with (self.data / "postgresql.conf").open("a", encoding="utf-8") as configuration:
                configuration.write(
                    f"\nlisten_addresses='127.0.0.1'\nport={self.port}\n"
                    "unix_socket_directories=''\nmax_connections=40\n"
                )
            self._command(
                "pg_ctl",
                "-D",
                str(self.data),
                "-l",
                str(self.directory / "server.log"),
                "-w",
                "-t",
                "30",
                "start",
            )
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_: object) -> None:
        if (self.data / "postmaster.pid").exists():
            try:
                self._command(
                    "pg_ctl", "-D", str(self.data), "-w", "-t", "15", "-m", "fast", "stop"
                )
            except (RuntimeError, subprocess.TimeoutExpired):
                self._command(
                    "pg_ctl", "-D", str(self.data), "-w", "-t", "15", "-m", "immediate", "stop"
                )
        self._temporary.cleanup()
