"""Exercise an extracted Linux release with external plugin resources."""

from __future__ import annotations

import argparse
import json
import os
import py_compile
import signal
import socket
import subprocess
import tempfile
import urllib.request
from pathlib import Path

PLUGIN = """from importlib.resources import files
from agents import Agent, RunConfig
from agents.testing import ScriptedModel, ModelStep, assistant_message
from agents.usage import Usage
from pydantic import BaseModel
from opsmesh.plugins import require

channels = require("channels")
api = channels.api
filesystem = require("filesystem")
execution = require("execution")

class ProbeRequest(BaseModel):
    value: str

async def verify():
    from agents.extensions.memory import SQLAlchemySession
    from agents.mcp import MCPServerManager
    from agents.sandbox import Manifest
    from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient
    from langfuse import Langfuse
    from openinference.instrumentation.openai_agents import (
        OpenAIAgentsInstrumentor,
    )
    resource = files(__package__).joinpath("message.txt").read_text()
    assert resource == "bundled resource"
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as root:
        client = UnixLocalSandboxClient(inherit_host_environment=False)
        sandbox = await client.create(manifest=Manifest(root=root))
        try:
            await sandbox.start()
            await sandbox.exec("printf ready > binary-probe.txt")
            assert Path(root, "binary-probe.txt").read_text() == "ready"
        finally:
            await sandbox.aclose()

@api.get("/probe")
async def probe() -> str:
    agent = Agent(name="release-probe", model=ScriptedModel([
        ModelStep(output=[assistant_message("ok")], usage=Usage(
            requests=1, input_tokens=3, output_tokens=1, total_tokens=4
        ))
    ]))
    result = await execution.run(
        agent, "hello", run_config=RunConfig(tracing_disabled=True)
    )
    assert result.context_wrapper.usage.total_tokens == 4
    return str(result.final_output)

@api.post("/validated", body=ProbeRequest)
async def validated() -> dict[str, str]:
    return {"status": "accepted"}
"""

DATABASE_PLUGIN = """from pathlib import Path
from sqlalchemy import MetaData
from opsmesh.plugins import PluginMetadata, require

database = require("database")
__plugin_meta__ = PluginMetadata(
    name=__name__,
    description="Independent migration smoke probe",
    usage="Validate and apply this plugin's own migration history",
    extra={"database": database.DatabaseSchema(
        MetaData(), Path(__file__).with_name("migrations"), "initial"
    )},
)

def verify():
    database.validate_migrations()
"""

MIGRATION = """from alembic import op
from sqlalchemy import Column, Integer

revision = "initial"
down_revision = None

def upgrade():
    op.create_table({table!r}, Column("id", Integer, primary_key=True))

def downgrade():
    op.drop_table({table!r})
"""


def database_plugins(directory: Path, dsn: str) -> list[dict]:
    """Declare two bytecode-only histories sharing the same revision name."""
    plugins = [plugin_entry("opsmesh.plugins.database", {"dsn": dsn})]
    for name in ("release_database_first", "release_database_second"):
        package = directory / name
        revisions = package / "migrations"
        revisions.mkdir(parents=True)
        (package / "__init__.py").write_text(DATABASE_PLUGIN)
        source = revisions / "initial.py"
        source.write_text(MIGRATION.format(table=name))
        py_compile.compile(
            str(source), cfile=str(source.with_suffix(".pyc")), doraise=True
        )
        source.unlink()
        plugins.append({"module": name, "required": True})
    return plugins


def plugin_entry(module: str, config: dict) -> dict:
    """Create an explicitly configured required plugin entry."""
    return {
        "module": module,
        "required": True,
        "config": config,
    }


def exercise_core(executable: Path, directory: Path) -> None:
    """Prove the host starts without plugins or their dependencies."""
    manifest = directory / "core.json"
    manifest.write_text(json.dumps({}))
    subprocess.run(
        [str(executable), "--manifest", str(manifest), "--check"],
        check=True,
        timeout=20,
        cwd=directory,
    )
    (directory / "core_probe.py").write_text(
        "from importlib.util import find_spec\n"
        "def verify():\n"
        "    for name in ('uvicorn', 'agents', 'sqlalchemy', "
        "'alembic', 'langfuse', "
        "'opsmesh.plugins.channels', 'opsmesh.plugins.execution', "
        "'opsmesh.plugins.database', "
        "'opsmesh.plugins.filesystem'):\n"
        "        assert find_spec(name) is None, name\n"
    )
    manifest.write_text(
        json.dumps(
            {
                "plugin_paths": [str(directory)],
                "plugins": [{"module": "core_probe", "required": True}],
            }
        )
    )
    subprocess.run(
        [
            str(executable),
            "--manifest",
            str(manifest),
            "--command",
            "core_probe:verify",
        ],
        check=True,
        timeout=20,
        cwd=directory,
    )


def exercise(
    executable: Path,
    directory: Path,
    *,
    plugin_path: Path,
    maintenance_only: bool = False,
) -> None:
    """Check bytecode migrations, HTTP, resources, and graceful SIGTERM."""
    package = directory / "release_probe"
    package.mkdir()
    (package / "__init__.py").write_text(PLUGIN)
    (package / "message.txt").write_text("bundled resource")
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    plugins = [
        plugin_entry(
            "opsmesh.plugins.filesystem", {"root": str(directory / "data")}
        ),
        plugin_entry(
            "opsmesh.plugins.channels",
            {
                "api": {
                    "host": "127.0.0.1",
                    "port": port,
                    "identities": [
                        {"id": "probe", "token_ref": "OPSMESH_SMOKE_TOKEN"}
                    ],
                },
            },
        ),
        plugin_entry("opsmesh.plugins.execution", {}),
    ]
    dsn = os.environ.get("OPSMESH_TEST_DSN")
    if dsn:
        plugins.extend(database_plugins(directory, dsn))
    plugins.append({"module": "release_probe", "required": True})
    manifest = directory / "host.json"
    manifest.write_text(
        json.dumps(
            {
                "plugin_paths": [str(plugin_path), str(directory)],
                "plugins": plugins,
            }
        )
    )
    environment = {**os.environ, "OPSMESH_SMOKE_TOKEN": "release-probe-token"}
    command = [str(executable), "--manifest", str(manifest)]
    subprocess.run(
        [*command, "--check"], env=environment, check=True, timeout=30
    )
    subprocess.run(
        [*command, "--command", "release_probe:verify"],
        env=environment,
        check=True,
        timeout=30,
    )
    if dsn:
        subprocess.run(
            [*command, "--command", "release_database_first:verify"],
            env=environment,
            check=True,
            timeout=30,
        )
    if maintenance_only:
        return
    if dsn:
        subprocess.run(
            [
                *command,
                "--command",
                "opsmesh.plugins.database.commands:migrate",
            ],
            env=environment,
            check=True,
            timeout=60,
        )
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
        notify_path = str(directory / "notify.sock")
        notify.bind(notify_path)
        notify.settimeout(30)
        environment["NOTIFY_SOCKET"] = notify_path
        with (directory / "host.log").open("w+") as log:
            process = subprocess.Popen(
                command, env=environment, stdout=log, stderr=log
            )
            try:
                assert notify.recv(4096) == b"READY=1"
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/plugins/release_probe/probe",
                    headers={"Authorization": "Bearer release-probe-token"},
                )
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({})
                )
                with opener.open(request, timeout=15) as response:
                    assert json.load(response) == "ok"
                validated = urllib.request.Request(
                    f"http://127.0.0.1:{port}/plugins/release_probe/validated",
                    data=b'{"value":"ok"}',
                    headers={
                        "Authorization": "Bearer release-probe-token",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with opener.open(validated, timeout=15) as response:
                    assert json.load(response) == {"status": "accepted"}
                process.send_signal(signal.SIGTERM)
                assert process.wait(timeout=30) == 0
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read())
                raise
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


def main() -> None:
    """Run a smoke check against the binary, not an installed source package."""
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--maintenance-only", action="store_true")
    parser.add_argument("--plugin-path", required=True, type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve()
    subprocess.run([str(executable), "--version"], check=True, timeout=10)
    with tempfile.TemporaryDirectory(prefix="opsmesh-core-") as directory:
        exercise_core(executable, Path(directory))
    with tempfile.TemporaryDirectory(prefix="opsmesh-smoke-") as directory:
        exercise(
            executable,
            Path(directory),
            plugin_path=args.plugin_path.resolve(),
            maintenance_only=args.maintenance_only,
        )
    print(
        "Maintenance smoke checks passed"
        if args.maintenance_only
        else "Release smoke checks passed"
    )


if __name__ == "__main__":
    main()
