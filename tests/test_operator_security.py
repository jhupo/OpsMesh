import io
import subprocess
import tarfile
from pathlib import Path

import pytest
from opsmesh_operator import commands, compose, deployments
from opsmesh_operator.cli import build_argument_parser, send_admin_request
from opsmesh_operator.contracts import ReleaseFile
from opsmesh_operator.deployments import ComposeDeployment
from opsmesh_operator.files import atomic_write, require_install_root
from opsmesh_operator.installation import Installation
from opsmesh_operator.installer import provision_updater
from opsmesh_operator.releases import ReleaseDownloader, extract_bundle

from tests.fixtures.releases import manifest


@pytest.mark.parametrize(
    "name,kind",
    [("../escape", "file"), ("/escape", "file"), ("link", "symlink"), ("device", "device")],
)
def test_release_archive_denies_escape_and_special_files(
    tmp_path: Path, name: str, kind: str
) -> None:
    path = tmp_path / "test.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo(name)
        member.size = 1 if kind == "file" else 0
        if kind == "symlink":
            member.type, member.linkname = tarfile.SYMTYPE, "../escape"
        elif kind == "device":
            member.type = tarfile.CHRTYPE
        archive.addfile(member, io.BytesIO(b"x") if kind == "file" else None)
    with pytest.raises(ValueError, match="Unsafe"):
        extract_bundle(path, tmp_path / "extract")
    assert not (tmp_path / "escape").exists()


def test_download_checksum_denial(tmp_path: Path, monkeypatch) -> None:
    source = ReleaseDownloader("jhupo/OpsMesh")

    def download(tag, name, path, *, limit):
        path.write_bytes(b"tampered")

    monkeypatch.setattr(source, "_download", download)
    record = ReleaseFile(name="bundle.tar.gz", sha256="a" * 64, size=8)
    with pytest.raises(ValueError, match="integrity"):
        source.download_file(manifest(), record, tmp_path)
    assert not (tmp_path / record.name).exists()


def test_subprocess_errors_do_not_disclose_stderr(monkeypatch) -> None:
    monkeypatch.setattr(
        commands.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "password=secret"),
    )
    with pytest.raises(RuntimeError) as caught:
        commands.run_command(["pg_dump"])
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("initialization_failed", [False, True])
def test_compose_waits_for_namespace_exit_before_reporting_infrastructure_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initialization_failed: bool
) -> None:
    calls: list[list[str]] = []
    installation = Installation(root=tmp_path, mode="compose", timeout_seconds=300)
    deployment = ComposeDeployment(installation)
    release = manifest()
    prefix = deployment.command(installation.release_dir(release))

    def run(args: list[str], *, timeout: int) -> str:
        assert timeout == 300
        calls.append(args[len(prefix) :])
        return ""

    def wait(command: list[str], *, service: str, timeout: int) -> None:
        assert command == prefix and timeout == 300 and service == "temporal-namespace"
        calls.append(["wait", service])
        if initialization_failed:
            raise RuntimeError("namespace initialization failed")

    monkeypatch.setattr(deployments, "run_command", run)
    monkeypatch.setattr(deployments, "wait_for_service_completion", wait)
    if initialization_failed:
        with pytest.raises(RuntimeError, match="namespace initialization failed"):
            deployment.infrastructure(release)
    else:
        deployment.infrastructure(release)
    assert calls[:2] == [
        ["up", "-d", "postgres", "redis", "temporal-namespace"],
        ["wait", "temporal-namespace"],
    ]
    if initialization_failed:
        assert len(calls) == 2
    else:
        assert calls[2] == ["up", "-d", "--no-deps", "--wait", "postgres", "redis", "temporal"]


@pytest.mark.parametrize("exit_code", [0, 1])
def test_initialization_checks_native_container_exit_even_after_it_has_stopped(
    monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    from types import SimpleNamespace

    import docker

    lifecycle: list[str] = []

    def wait(*, timeout: int, condition: str) -> dict[str, int]:
        assert timeout == 180 and condition == "not-running"
        lifecycle.append("wait")
        return {"StatusCode": exit_code}

    container = SimpleNamespace(
        labels={"com.docker.compose.service": "temporal-namespace"}, wait=wait
    )
    client = SimpleNamespace(
        containers=SimpleNamespace(get=lambda identifier: container),
        close=lambda: lifecycle.append("close"),
    )

    def query(command: list[str], *, timeout: int) -> str:
        assert command == ["docker", "compose", "ps", "--all", "--quiet", "temporal-namespace"]
        return "stopped-container-id\n"

    monkeypatch.setattr(compose, "run_command", query)
    monkeypatch.setattr(docker, "from_env", lambda **kwargs: client)
    if exit_code:
        with pytest.raises(RuntimeError, match="initialization failed"):
            compose.wait_for_service_completion(
                ["docker", "compose"], service="temporal-namespace", timeout=180
            )
    else:
        compose.wait_for_service_completion(
            ["docker", "compose"], service="temporal-namespace", timeout=180
        )
    assert lifecycle == ["wait", "close"]


def test_admin_client_refuses_cleartext_remote_token_transport() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        send_admin_request("http://remote.example.test", "GET", "/api/v1/admin/system/version")


def test_installation_rejects_broad_root_and_preserves_atomic_state(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        require_install_root(Path.home())
    path = tmp_path / "state.json"
    atomic_write(path, '{"phase":"first"}')
    atomic_write(path, '{"phase":"second"}')
    assert path.read_text() == '{"phase":"second"}'
    installation = Installation(root=tmp_path, mode="compose")
    installation.save()
    assert Installation.load(tmp_path) == installation


def test_cli_requires_exact_plan_fingerprint_for_approval() -> None:
    with pytest.raises(SystemExit):
        build_argument_parser().parse_args(
            ["update", "apply", "--plan", "00000000-0000-0000-0000-000000000001"]
        )


def test_updater_is_an_independent_copy_without_secrets(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "BUILD.json").write_text('{"tag":"v0.1.0"}')
    (bundle / "opsmesh-server").write_text("launcher")
    (bundle / ".env").write_text("secret")
    updater = tmp_path / "updater"
    provision_updater(bundle, updater)
    assert not (updater / ".env").exists()
    (bundle / "opsmesh-server").write_text("changed app")
    assert (updater / "opsmesh-server").read_text() == "launcher"
    provision_updater(bundle, updater)
    (bundle / "BUILD.json").write_text('{"tag":"v0.2.0"}')
    with pytest.raises(ValueError, match="different release"):
        provision_updater(bundle, updater)


def test_interrupted_updater_copy_is_not_silently_reused(tmp_path: Path) -> None:
    (tmp_path / ".updater.staging").mkdir()
    with pytest.raises(ValueError, match="Interrupted"):
        provision_updater(tmp_path / "bundle", tmp_path / "updater")
