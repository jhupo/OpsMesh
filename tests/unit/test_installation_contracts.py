"""Verify framework-only defaults and disposable installer test artifacts."""

import hashlib
import io
import json
import runpy
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_default_manifest_needs_no_plugins_or_credentials():
    from opsmesh.app import HostManifest

    manifest = HostManifest.model_validate_json(
        (ROOT / "deploy/host.example.json").read_text(encoding="utf-8")
    )
    assert manifest.plugins == ()
    assert manifest.plugin_paths == ()


def test_installation_validation_refuses_non_disposable_hosts(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    script = runpy.run_path(str(ROOT / "scripts/test_installation.py"))
    with pytest.raises(SystemExit, match="disposable GitHub Actions runner"):
        script["main"]()


@pytest.mark.parametrize("fail_start", [False, True])
def test_installer_probe_archive_retains_payload_and_checksums(
    tmp_path, fail_start
):
    script = runpy.run_path(str(ROOT / "scripts/test_installation.py"))
    archive = tmp_path / "opsmesh-0.1.0-linux-x86_64.tar.gz"
    root = archive.name.removesuffix(".tar.gz")
    service = b"[Service]\nExecStart=/opt/opsmesh/current/opsmesh\n"
    contents = {
        "opsmesh": b"executable",
        "deploy/systemd/opsmesh.service": service,
        "deploy/host.example.json": json.dumps({"plugins": []}).encode(),
    }
    with tarfile.open(archive, "w:gz") as output:
        for relative, content in contents.items():
            member = tarfile.TarInfo(f"{root}/{relative}")
            member.size = len(content)
            member.mode = 0o755 if relative == "opsmesh" else 0o644
            output.addfile(member, io.BytesIO(content))
    target = tmp_path / "changed"
    target.mkdir()
    changed, checksums = script["changed_archive"](
        archive, target, fail_start=fail_start
    )
    with changed.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    assert checksums.read_text() == f"{digest}  {changed.name}\n"
    with tarfile.open(changed) as output:
        assert output.getmember(f"{root}/opsmesh").mode == 0o755
        assert output.extractfile(f"{root}/opsmesh").read() == b"executable"
        actual = output.extractfile(
            f"{root}/deploy/systemd/opsmesh.service"
        ).read()
        if fail_start:
            assert b"Type=notify" in actual
            assert b"ExecStart=/usr/bin/false" in actual
        else:
            assert actual == service
