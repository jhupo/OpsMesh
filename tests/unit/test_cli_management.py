"""Test the independently installed service. No business plugins required."""

import base64
import hashlib
import json
import sys
import uuid
from pathlib import Path
from zipfile import ZipFile

import pytest
from opsmesh_cli.config import Fault, Settings
from opsmesh_cli.main import main
from opsmesh_cli.plugin.manager import Manager
from opsmesh_cli.socket import validate


def wheel(
    inbox, v, *, name="probe", dist=None, depends="", broken=False, extra=None
):
    dist = dist or name
    stem = dist.replace("-", "_")
    prefix = f"{stem}-{v}.dist-info"
    path = inbox / f"{stem}-{v}-py3-none-any.whl"
    requires = f"Requires-Dist: {depends}\n" if depends else ""
    files = {
        f"{name}/__init__.py": (
            'raise RuntimeError("fixture failed")'
            if broken
            else f"VERSION={v!r}"
        ).encode(),
        f"{name}/greeting.txt": b"hello",
        f"{name}/defaults.toml": b'greeting="hello"',
        f"{prefix}/METADATA": f"Metadata-Version: 2.1\nName: {dist}\nVersion: {v}\nRequires-Python: >=3.11\nRequires-Dist: opsmesh>=0.5,<1\n{requires}".encode(),
        f"{prefix}/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    if extra:
        files.update(extra)

    def entry(key, data):
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(data).digest())
            .decode()
            .rstrip("=")
        )
        return f"{key},sha256={digest},{len(data)}\n"

    files[f"{prefix}/RECORD"] = (
        "".join(entry(k, b) for k, b in files.items()) + f"{prefix}/RECORD,,\n"
    ).encode()
    with ZipFile(path, "w") as archive:
        for n, b in files.items():
            archive.writestr(n, b)
    return {
        "wheel": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


@pytest.fixture
def host(tmp_path):
    cfg = Settings(
        *(tmp_path / k for k in ("state", "run", "log", "inbox", "backups")),
        group="",
        restart_safe=("probe",),
    )
    cfg.data.mkdir()
    cfg.inbox.mkdir()
    return Manager(cfg)


def call(host, method, request_id=None, **args):
    return host.dispatch(
        1000,
        validate(
            {
                "v": 1,
                "request_id": request_id or str(uuid.uuid4()),
                "method": method,
                "params": args,
            }
        ),
    )


def test_installed_package_and_offline_cli(tmp_path, capsys):
    import socket

    import opsmesh_cli.socket as rpc

    assert socket is not rpc
    cfg = tmp_path / "cfg.toml"
    cfg.write_text(
        "\n".join(
            f"{key}={json.dumps(str(tmp_path / key))}"
            for key in ("data", "run", "inbox", "backups", "logs")
        )
    )
    assert main(["--config", str(cfg), "--json", "plugin", "list"]) == 6
    assert (
        json.loads(capsys.readouterr().out)["error"]["code"]
        == "service_unavailable"
    )
    assert not (tmp_path / "run").exists()


def test_single_version_contents_and_retention(host):
    rid = str(uuid.uuid4())
    one = wheel(host.config.inbox, "1")
    first = call(host, "plugin.install", rid, **one)
    assert call(host, "plugin.install", rid, **one) == first
    base = host.fs.plugin("probe")
    saved = base / "local.toml"
    saved.write_text("keep")
    for v in ("2", "3"):
        call(host, "plugin.upgrade", id="probe", **wheel(host.config.inbox, v))
        assert (base / "__init__.py").read_text() == f"VERSION={v!r}"
        assert not list((host.config.data / "staging").iterdir())
        assert call(host, "plugin.status", id="probe")["installed_version"] == v
        assert saved.read_text() == "keep"
    call(host, "plugin.uninstall", id="probe")
    assert not (base / "__init__.py").exists() and saved.exists()
    assert call(host, "plugin.status", id="probe")["state"] == "retained"
    with pytest.raises(Fault):
        call(host, "plugin.delete", id="probe", confirm="other")
    call(host, "plugin.delete", id="probe", confirm="probe")
    assert not base.exists()


def test_staged_input_never_escapes_or_executes(host):
    with pytest.raises(Fault):
        call(
            host,
            "plugin.install",
            **wheel(host.config.inbox, "1", extra={"probe/../oops": b"x"}),
        )
    assert not call(host, "plugin.list")
    one = wheel(host.config.inbox, "1", broken=True)
    assert call(host, "plugin.install", plan=True, **one)["restart"] is False
    assert not call(host, "plugin.list")
    assert call(host, "plugin.install", **one)["phase"] == "succeeded"


def test_local_files_and_collisions(host):
    one = wheel(host.config.inbox, "1", dist="acme-reporter")
    call(host, "plugin.install", **one)
    assert (
        call(host, "plugin.status", id="probe")["distribution"]
        == "acme-reporter"
    )
    base = host.fs.plugin("probe")
    (base / "local.toml").write_text("owner")
    two = wheel(
        host.config.inbox,
        "2",
        dist="acme-reporter",
        extra={"probe/local.toml": b"package"},
    )
    with pytest.raises(Fault, match="overwrite local"):
        call(host, "plugin.upgrade", id="probe", **two)
    (base / "defaults.toml").write_text("edited")
    with pytest.raises(Fault, match="Changed package file"):
        call(host, "plugin.upgrade", id="probe", **two)
    assert call(host, "plugin.uninstall", id="probe")["preserved"] == [
        "defaults.toml"
    ]
    assert (base / "local.toml").read_text() == "owner"
    assert (base / "defaults.toml").read_text() == "edited"


def test_ambiguous_package_rejected_before_writing(host):
    with pytest.raises(Fault, match="single top-level"):
        call(host, "plugin.install", **wheel(host.config.inbox, "1", extra={"other/__init__.py": b""}))
    assert not host.fs.plugin("probe").exists()
    assert call(host, "plugin.list") == []


def test_interrupted_file_publish_resumes(host, monkeypatch):
    import opsmesh_cli.storage.filesystem as fs

    call(host, "plugin.install", **wheel(host.config.inbox, "1"))
    local = host.fs.plugin("probe") / "customer.toml"
    local.write_text("custom")
    move = fs.move
    def interrupt(src, dst):
        move(src, dst)
        raise OSError("interrupted after durable rename")
    with monkeypatch.context() as patch:
        patch.setattr(fs, "move", interrupt)
        with pytest.raises(OSError, match="interrupted"):
            call(host, "plugin.upgrade", id="probe", **wheel(host.config.inbox, "2"))
    recovered = Manager(host.config)
    recovered.advance()
    assert call(recovered, "plugin.status", id="probe")["installed_version"] == "2"
    assert local.read_text() == "custom"
    assert [p.name for p in (local.parent / ".opsmesh-metadata").iterdir()] == ["probe-2.dist-info"]


def test_missing_database_requires_recovery(tmp_path):
    from opsmesh_cli.storage.database import Database

    (tmp_path / "plugins").mkdir()
    with pytest.raises(Fault, match="Database missing"):
        Database(tmp_path)
    assert not (tmp_path / "state.sqlite3").exists()


def test_dependencies_and_running_policy(host):
    call(host, "plugin.install", **wheel(host.config.inbox, "1"))
    call(
        host,
        "plugin.install",
        **wheel(host.config.inbox, "1", name="consumer", depends="probe==1"),
    )
    with pytest.raises(Fault, match="requires probe"):
        call(
            host, "plugin.upgrade", id="probe", **wheel(host.config.inbox, "2")
        )
    with pytest.raises(Fault, match="requires probe"):
        call(host, "plugin.enable", id="consumer")
    call(host, "plugin.enable", id="probe")
    with pytest.raises(Fault, match="restart"):
        call(host, "plugin.start", id="probe")
    op = call(host, "plugin.start", id="probe", allow_restart=True)
    new = Manager(host.config)
    records, names = new.advance()
    assert names == {"probe"}
    new.ready({n: records[n]["version"] for n in names})
    assert (
        call(new, "operation.show", op_id=op["op_id"])["phase"] == "succeeded"
    )
    call(new, "plugin.disable", id="probe")
    status = call(new, "plugin.status", id="probe")
    assert not status["enabled"] and status["running_version"] == "1"


def test_failed_trial_recovers_previous_once(host):
    one = wheel(host.config.inbox, "1")
    call(host, "plugin.install", **one)
    host.running = {"probe": "1"}
    op = call(
        host,
        "plugin.upgrade",
        id="probe",
        allow_restart=True,
        **wheel(host.config.inbox, "2", broken=True),
    )
    entry = host.fs.plugin("probe") / "__init__.py"
    original = entry.read_bytes()
    candidate = Manager(host.config)
    assert candidate.advance()[0]["probe"]["version"] == "2"
    fallback = Manager(host.config)
    records, names = fallback.advance()
    assert records["probe"]["version"] == "1" and names == {"probe"}
    assert entry.read_bytes() == original
    fallback.ready({"probe": "1"})
    assert (
        call(fallback, "operation.show", op_id=op["op_id"])["phase"] == "failed"
    )


def test_committed_upgrade_resumes_cleanup_not_rollback(host, monkeypatch):
    call(host, "plugin.install", **wheel(host.config.inbox, "1"))

    def fail(*args):
        raise PermissionError("fixture")

    monkeypatch.setattr(host.fs, "cleanup", fail)
    op = call(
        host, "plugin.upgrade", id="probe", **wheel(host.config.inbox, "2")
    )
    assert op["phase"] == "cleanup"
    assert call(host, "plugin.status", id="probe")["installed_version"] == "2"
    new = Manager(host.config)
    new.advance()
    assert (
        call(new, "operation.show", op_id=op["op_id"])["phase"] == "succeeded"
    )
    assert not list((host.config.data / "staging").iterdir())


@pytest.mark.skipif(
    sys.platform != "linux", reason="POSIX lock and offline service identity"
)
def test_offline_backup_and_restore(host):
    import os
    from argparse import Namespace

    from opsmesh_cli.maintenance import maintain

    if os.getuid() == 0:
        pytest.skip("service user required")
    call(host, "plugin.install", **wheel(host.config.inbox, "1"))
    snap = maintain(host.config, Namespace(action="backup"))["snapshot"]
    call(host, "plugin.uninstall", id="probe")
    maintain(
        host.config, Namespace(action="restore", snapshot=snap, confirm=snap)
    )
    assert call(host, "plugin.status", id="probe")["installed_version"] == "1"


@pytest.mark.skipif(
    sys.platform != "linux", reason="Linux Unix socket credentials and signals"
)
def test_foreground_service_with_supervised_restart(host, tmp_path):
    import asyncio
    import os
    import subprocess
    import time

    from opsmesh_cli.socket import request

    if os.getuid() == 0:
        pytest.skip("daemon refuses root")
    cfg = tmp_path / "service.toml"
    cfg.write_text(
        "\n".join(
            f"{k}={json.dumps(v, default=str)}"
            for k, v in vars(host.config).items()
        )
    )
    source = "from opsmesh_cli.main import main; raise SystemExit(main())"
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(str(Path(p).resolve()) for p in sys.path),
        "NOTIFY_SOCKET": "",
        "REPORT_INTERVAL": "42",
    }

    def rpc(method, **params):
        return asyncio.run(
            request(
                host.config.sock,
                {
                    "v": 1,
                    "method": method,
                    "request_id": str(uuid.uuid4()),
                    "params": params,
                },
            )
        )

    log = tmp_path / "stderr"

    def start():
        with log.open("ab") as out:
            return subprocess.Popen(
                [sys.executable, "-c", source, "--config", str(cfg), "serve"],
                env=env,
                cwd=tmp_path,
                stdout=out,
                stderr=subprocess.STDOUT,
            )

    def wait_ready(proc):
        for _ in range(150):
            if proc.poll() is not None:
                break
            try:
                rpc("service.status")
                return
            except Fault:
                time.sleep(0.08)
        service_log = host.config.logs / "opsmeshd.log"
        pytest.fail(
            log.read_text()
            + (service_log.read_text() if service_log.exists() else "")
        )

    proc = start()
    try:
        wait_ready(proc)
        assert rpc("plugin.list") == []
        code = b"""from importlib.resources import files
import tomllib
from importlib.metadata import version
assert version("acme-probe") == "1"
from pydantic import BaseModel
from opsmesh import get_plugin_config
class Settings(BaseModel):
    report_interval: int = 5
assert get_plugin_config(Settings).report_interval == 42
assert tomllib.loads(files(__package__).joinpath("defaults.toml").read_text())["greeting"] == "hello"
"""
        rpc(
            "plugin.install",
            **wheel(
                host.config.inbox,
                "1",
                dist="acme-probe",
                extra={"probe/__init__.py": code},
            ),
        )
        op = rpc("plugin.enable", id="probe", now=True, allow_restart=True)
        assert proc.wait(15) == 75
        proc = start()
        wait_ready(proc)
        assert rpc("operation.show", op_id=op["op_id"])["phase"] == "succeeded"
        assert rpc("plugin.status", id="probe")["running_version"] == "1"
        op = rpc(
            "plugin.upgrade",
            id="probe",
            allow_restart=True,
            **wheel(host.config.inbox, "2", dist="acme-probe", broken=True),
        )
        assert proc.wait(15) == 75
        proc = start()
        assert proc.wait(15) == 75
        proc = start()
        wait_ready(proc)
        assert rpc("operation.show", op_id=op["op_id"])["phase"] == "failed"
        assert rpc("plugin.status", id="probe")["running_version"] == "1"
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
