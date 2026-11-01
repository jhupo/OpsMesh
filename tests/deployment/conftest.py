"""Offline inputs for testing release assembly and installer integrity checks."""

import runpy
import tarfile
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def release_builder():
    return runpy.run_path(str(ROOT / "scripts/build_linux_release.py"))


@pytest.fixture
def release_inputs(tmp_path):
    wheel = tmp_path / "opsmesh-0.3.0-py3-none-any.whl"
    with ZipFile(wheel, "w") as package:
        package.writestr("opsmesh/__init__.py", '__version__ = "0.3.0"\n')
        package.writestr(
            "opsmesh-0.3.0.dist-info/METADATA",
            "Metadata-Version: 2.3\nName: opsmesh\nVersion: 0.3.0\n",
        )
        package.writestr(
            "opsmesh-0.3.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
    requirements = tmp_path / "requirements.lock"
    requirements.write_bytes(b"example==1.0 --hash=sha256:" + b"a" * 64 + b"\r\n")
    return wheel, requirements


@pytest.fixture
def release_bundle(tmp_path, release_builder, release_inputs):
    wheel, requirements = release_inputs
    archive = release_builder["assemble_bundle"](wheel, requirements, tmp_path / "bundles")
    destination = tmp_path / "extracted"
    with tarfile.open(archive) as package:
        for member in package.getmembers():
            if member.isfile():
                target = destination / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(package.extractfile(member).read())
    return destination / "opsmesh-0.3.0-linux"
