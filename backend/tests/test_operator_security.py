import io
import subprocess
import tarfile
from pathlib import Path

import pytest
from opsmesh_operator import commands, releases
from opsmesh_operator.cli import parser, request
from opsmesh_operator.contracts import ReleaseFile
from opsmesh_operator.files import atomic_write, require_install_root
from opsmesh_operator.installation import Installation
from opsmesh_operator.releases import ReleaseSource, extract_bundle

from backend.tests.test_platform_updates import manifest


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
    source = ReleaseSource("jhupo/OpsMesh")

    def download(tag, name, path, *, limit):
        path.write_bytes(b"tampered")

    monkeypatch.setattr(source, "_download", download)
    record = ReleaseFile(name="bundle.tar.gz", sha256="a" * 64, size=8)
    with pytest.raises(ValueError, match="integrity"):
        source.download_file(manifest(), record, tmp_path)
    assert not (tmp_path / record.name).exists()


def test_provenance_verification_pins_repository_workflow_tag_and_commit(
    tmp_path: Path, monkeypatch
) -> None:
    calls = []
    monkeypatch.setattr(releases, "run_command", lambda argv, **kwargs: calls.append(argv))
    ReleaseSource("jhupo/OpsMesh").verify(tmp_path / "manifest.json", "v0.1.0", commit="a" * 40)
    assert calls[0][-2:] == ["--source-digest", "a" * 40]
    assert "--deny-self-hosted-runners" in calls[0]
    assert "jhupo/OpsMesh/.github/workflows/release-publish.yml" in calls[0]
    assert "refs/tags/v0.1.0" in calls[0]


def test_subprocess_errors_do_not_disclose_stderr(monkeypatch) -> None:
    monkeypatch.setattr(
        commands.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "password=secret"),
    )
    with pytest.raises(RuntimeError) as caught:
        commands.run_command(["pg_dump"])
    assert "secret" not in str(caught.value)


def test_admin_client_refuses_cleartext_remote_token_transport() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        request("http://remote.example.test", "GET", "/api/v1/admin/system/version")


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
        parser().parse_args(["update", "apply", "--plan", "00000000-0000-0000-0000-000000000001"])
