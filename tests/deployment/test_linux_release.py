"""Validate the distributable artifact without building or downloading dependencies."""

import shutil
import tarfile
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_release_contains_only_installation_artifacts_with_complete_checksums(
    tmp_path, release_builder, release_inputs, monkeypatch
):
    wheel, requirements = release_inputs
    assembly = release_builder["assemble_bundle"]
    project = tmp_path / "project"
    for relative in (*release_builder["DEPLOYMENT_FILES"], "LICENSE"):
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    for relative in ("src/private.py", "tests/private.py", ".env", ".venv/bin/python", "uv.lock"):
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("must not be shipped", encoding="utf-8")
    monkeypatch.setitem(assembly.__globals__, "ROOT", project)
    archive = assembly(wheel, requirements, tmp_path / "output")
    assert archive.name == "opsmesh-0.3.0-linux.tar.gz"
    prefix = "opsmesh-0.3.0-linux/"
    with tarfile.open(archive) as package:
        files = {
            member.name.removeprefix(prefix): package.extractfile(member).read()
            for member in package.getmembers()
            if member.isfile()
        }
        for member in package.getmembers():
            assert not member.issym() and not member.islnk()
            assert member.uid == member.gid == 0
            if member.name.endswith(".sh"):
                assert member.mode == 0o755
    expected = {
        *release_builder["DEPLOYMENT_FILES"],
        "install.sh",
        "VERSION",
        "README.md",
        "LICENSE",
        "requirements.lock",
        "SHA256SUMS",
        f"packages/{wheel.name}",
    }
    assert set(files) == expected
    manifest = dict(line.split("  ", 1)[::-1] for line in files["SHA256SUMS"].decode().splitlines())
    assert set(manifest) == expected - {"SHA256SUMS"}
    for relative, digest in manifest.items():
        assert sha256(files[relative]).hexdigest() == digest
    assert files[f"packages/{wheel.name}"] == wheel.read_bytes()
    assert files["requirements.lock"] == requirements.read_bytes().replace(b"\r\n", b"\n")
    assert all(b"\r\n" not in data for name, data in files.items() if name.endswith(".sh"))
    assert archive.with_suffix(".gz.sha256").read_text(encoding="utf-8") == (
        f"{sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
    )


def test_wheel_version_mismatch_is_rejected_before_publication(
    tmp_path, release_builder, release_inputs
):
    wheel, requirements = release_inputs
    with ZipFile(wheel, "w") as package:
        package.writestr("opsmesh-0.3.0.dist-info/METADATA", "Name: opsmesh\nVersion: 9.9.9\n")
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="versions differ"):
        release_builder["assemble_bundle"](wheel, requirements, output)
    assert not list(output.glob("*.tar.gz"))
