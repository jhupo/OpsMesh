"""Online updates never execute unverified or redirected foreign release content."""

import hashlib
import io
import os
import tarfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.maintenance import updates
from opsmesh.maintenance.updates import ReleaseUpdateService

VERSION = "99.0.0"
NAME = f"opsmesh-{VERSION}-linux.tar.gz"
URL = f"https://github.com/jhupo/OpsMesh/releases/download/v{VERSION}/{NAME}"


def archive_bytes(*, extra=None, corrupt=False):
    files = {"VERSION": (VERSION + "\n").encode(), "install.sh": b"#!/bin/bash\nexit 0\n"}
    manifest = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in files.items()
    )
    files["SHA256SUMS"] = manifest.encode()
    if corrupt:
        files["install.sh"] = b"changed-after-manifest"
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:gz") as bundle:
        for name, data in files.items():
            member = tarfile.TarInfo(f"opsmesh-{VERSION}-linux/{name}")
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
        if extra:
            member = tarfile.TarInfo(extra[0])
            member.type = extra[1]
            member.linkname = "/etc/passwd" if member.issym() else ""
            bundle.addfile(member)
    return result.getvalue()


def release_metadata(content, *, digest=True):
    asset = {"name": NAME, "browser_download_url": URL}
    if digest:
        asset["digest"] = "sha256:" + hashlib.sha256(content).hexdigest()
    return {
        "tag_name": f"v{VERSION}",
        "assets": [asset, {"name": NAME + ".sha256", "browser_download_url": URL + ".sha256"}],
    }


@pytest.mark.parametrize("digest", [True, False])
async def test_download_and_extract_verified_release_with_api_or_checksum_digest(tmp_path, digest):
    content = archive_bytes()

    def handle(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json=release_metadata(content, digest=digest))
        if request.url.path.endswith(".sha256"):
            return httpx.Response(200, text=f"{hashlib.sha256(content).hexdigest()}  {NAME}\n")
        return httpx.Response(200, content=content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        updater = ReleaseUpdateService(client)
        release = await updater.check()
        assert release.available
        archive = await updater.download(release, tmp_path / "cache")
        root = updater.extract(archive, tmp_path / "extracted", VERSION)
        assert (root / "install.sh").read_bytes().startswith(b"#!/bin/bash")


@pytest.mark.parametrize(
    "version",
    ["0" * 100_000 + ".0.0", "v1.0.0/../other", "1.0.0rc1"],
    ids=["oversized", "traversal", "prerelease"],
)
async def test_invalid_version_is_rejected_before_network_request(version):
    def no_request(_request):
        pytest.fail("Invalid versions must not issue a request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_request)) as client:
        with pytest.raises(OpsMeshError) as error:
            await ReleaseUpdateService(client).check(version)
        assert error.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize(
    "tag",
    ["v" + "0" * 100_000 + ".0.0", "v1.0.0rc1", 123],
    ids=["oversized", "prerelease", "nontext"],
)
async def test_invalid_remote_tag_is_rejected_before_asset_download(tag):
    requests = []

    def handle(request):
        requests.append(request.url.host)
        metadata = release_metadata(archive_bytes())
        metadata["tag_name"] = tag
        return httpx.Response(200, json=metadata)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(OpsMeshError) as error:
            await ReleaseUpdateService(client).check()
        assert error.value.code == "UPDATE_INVALID"
    assert requests == ["api.github.com"]


async def test_bad_download_does_not_replace_previously_verified_asset(tmp_path):
    good = archive_bytes()

    def handle(request):
        return (
            httpx.Response(200, json=release_metadata(good))
            if request.url.host == "api.github.com"
            else httpx.Response(200, content=b"corrupted")
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        updater = ReleaseUpdateService(client)
        release = await updater.check(VERSION)
        previous = tmp_path / NAME
        previous.write_bytes(good)
        with pytest.raises(OpsMeshError, match="checksum differs"):
            await updater.download(release, tmp_path)
        assert previous.read_bytes() == good
        forged = release.model_copy(update={"asset_name": "../escape.tar.gz"})
        with pytest.raises(OpsMeshError, match="identity"):
            await updater.download(forged, tmp_path)


async def test_redirect_to_private_host_is_rejected_before_request(tmp_path):
    seen = []

    def handle(request):
        seen.append(request.url.host)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(OpsMeshError, match="location"):
            await ReleaseUpdateService(client).check()
    assert seen == ["api.github.com"]


@pytest.mark.parametrize(
    "extra",
    [
        (f"opsmesh-{VERSION}-linux/../escape", tarfile.REGTYPE),
        ("/etc/passwd", tarfile.REGTYPE),
        (f"opsmesh-{VERSION}-linux/link", tarfile.SYMTYPE),
        (f"opsmesh-{VERSION}-linux/device", tarfile.CHRTYPE),
        (f"opsmesh-{VERSION}-linux/uncovered", tarfile.REGTYPE),
    ],
)
def test_unsafe_or_uncovered_archive_never_reaches_installer(tmp_path, extra):
    archive = tmp_path / "release.tar.gz"
    archive.write_bytes(archive_bytes(extra=extra))
    with pytest.raises(OpsMeshError):
        ReleaseUpdateService.extract(archive, tmp_path / "extract", VERSION)
    assert not (tmp_path / "escape").exists()


def test_corrupt_archive_or_content_reports_typed_error(tmp_path):
    archive = tmp_path / "release.tar.gz"
    for data in (b"not a tar", archive_bytes(corrupt=True)):
        archive.write_bytes(data)
        target = tmp_path / hashlib.sha256(data).hexdigest()
        with pytest.raises(OpsMeshError) as error:
            ReleaseUpdateService.extract(archive, target, VERSION)
        assert error.value.code == "UPDATE_INVALID"


async def test_installer_reuses_existing_upgrade_entrypoint_with_explicit_arguments(
    tmp_path, monkeypatch
):
    content = archive_bytes()
    calls = []

    def handle(request):
        return (
            httpx.Response(200, json=release_metadata(content))
            if request.url.host == "api.github.com"
            else httpx.Response(200, content=content)
        )

    async def execute(*arguments):
        assert Path(arguments[1]).is_file()
        calls.append(arguments)

        async def wait():
            return 0

        return SimpleNamespace(wait=wait)

    monkeypatch.setattr(updates, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(updates, "os", SimpleNamespace(geteuid=lambda: 0, replace=os.replace))
    monkeypatch.setattr(updates.asyncio, "create_subprocess_exec", execute)
    monkeypatch.setattr(ReleaseUpdateService, "installed_version", lambda self, instance: "0.1.0")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        updater = ReleaseUpdateService(client)
        release = await updater.check()
        await updater.install(
            release, tmp_path, instance="test-instance", mode="local", no_start=True
        )
    assert len(calls) == 1
    assert calls[0][2:] == (
        "--upgrade",
        "--mode",
        "local",
        "--instance",
        "test-instance",
        "--expect-installed-version",
        "0.1.0",
        "--no-start",
    )


async def test_update_refuses_downgrading_a_newer_target_instance(tmp_path, monkeypatch):
    content = archive_bytes()
    monkeypatch.setattr(updates, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(updates, "os", SimpleNamespace(geteuid=lambda: 0))
    monkeypatch.setattr(ReleaseUpdateService, "installed_version", lambda self, instance: "100.0.0")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=release_metadata(content))
        )
    ) as client:
        updater = ReleaseUpdateService(client)
        release = await updater.check()
        with pytest.raises(OpsMeshError, match="target instance"):
            await updater.install(release, tmp_path, instance="newer")
    assert not list(tmp_path.iterdir())


async def test_download_limits_and_network_errors_are_safe(tmp_path):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"oversized"))
    ) as client:
        with pytest.raises(OpsMeshError, match="size limit"):
            await ReleaseUpdateService(client)._download(URL, tmp_path / "limited", 2)

    def failed(request):
        raise httpx.ConnectError("sensitive proxy details", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(failed)) as client:
        with pytest.raises(OpsMeshError) as error:
            await ReleaseUpdateService(client).check()
        assert error.value.code == "UPDATE_UNAVAILABLE"
        assert "sensitive" not in str(error.value)
