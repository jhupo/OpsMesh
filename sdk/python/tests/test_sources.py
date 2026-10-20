"""Contract checks for static Extension Center release imports."""

import asyncio
import hashlib
import json

import pytest
from pydantic import ValidationError as SchemaError

from opsmesh_sdk.errors import ValidationError
from opsmesh_sdk.sources.github import ReleaseManifest, configured_repository, parse_manifest
from opsmesh_sdk.sources.packages import verify_package


def test_asset_must_belong_to_configured_repository_and_tag() -> None:
    from opsmesh_sdk.sources.github import ReleaseAsset, asset_url

    url = "https://github.com/example/extensions/releases/download/v1/package.whl"
    asset = ReleaseAsset(name="package.whl", browser_download_url=url)
    assert asset_url(asset, repository="example/extensions", tag="v1") == url
    with pytest.raises(ValidationError):
        asset_url(asset, repository="other/extensions", tag="v1")
    with pytest.raises(ValidationError):
        asset_url(asset, repository="example/extensions", tag="v2")


def test_download_redirect_cannot_escape_github_allowlist(monkeypatch) -> None:
    import httpx

    from opsmesh_sdk.sources import github as releases

    actual_client = httpx.Client
    requests: list[str] = []

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    monkeypatch.setattr(
        releases.httpx,
        "Client",
        lambda **kwargs: actual_client(**kwargs, transport=httpx.MockTransport(respond)),
    )
    with pytest.raises(ValidationError):
        releases.github_asset("https://github.com/example/extensions/releases/download/v1/a.whl")
    assert len(requests) == 1


def test_download_enforces_streaming_size_limit(monkeypatch) -> None:
    import httpx

    from opsmesh_sdk.sources import github as releases

    actual_client = httpx.Client
    monkeypatch.setattr(
        releases.httpx,
        "Client",
        lambda **kwargs: actual_client(
            **kwargs,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"abcdef")),
        ),
    )
    with pytest.raises(ValidationError):
        releases.github_asset(
            "https://github.com/example/extensions/releases/download/v1/a.whl", maximum_bytes=5
        )


def manifest_asset(key: str = "mail", asset: str = "mail.whl") -> dict[str, object]:
    return {
        "type": "extension",
        "key": key,
        "name": key.title(),
        "configuration": {},
        "asset": asset,
        "sha256": "a" * 64,
    }


def test_release_manifest_rejects_duplicate_keys_and_assets() -> None:
    with pytest.raises(SchemaError):
        ReleaseManifest.model_validate(
            {"schema_version": 1, "packages": [manifest_asset(), manifest_asset()]}
        )
    with pytest.raises(SchemaError):
        ReleaseManifest.model_validate(
            {
                "schema_version": 1,
                "packages": [manifest_asset(), manifest_asset("calendar")],
            }
        )


def test_release_manifest_rejects_same_key_across_package_types() -> None:
    channel = manifest_asset()
    channel["type"] = "channel"
    channel["asset"] = "channel.whl"
    with pytest.raises(SchemaError):
        ReleaseManifest.model_validate(
            {"schema_version": 1, "packages": [manifest_asset(), channel]}
        )


def test_release_manifest_rejects_paths_and_unknown_fields() -> None:
    package = manifest_asset()
    package["asset"] = "../mail.whl"
    with pytest.raises(SchemaError):
        ReleaseManifest.model_validate({"schema_version": 1, "packages": [package]})

    package = manifest_asset()
    package["execute"] = True
    with pytest.raises(ValidationError):
        parse_manifest(json.dumps({"schema_version": 1, "packages": [package]}).encode())


def test_repository_and_digest_contract() -> None:
    assert configured_repository("opsmesh/extensions") == "opsmesh/extensions"
    for invalid in (None, "https://github.com/opsmesh/extensions", "../extensions"):
        with pytest.raises(ValidationError):
            configured_repository(invalid)

    package = b"static extension archive"
    verify_package(package, hashlib.sha256(package).hexdigest())
    with pytest.raises(ValidationError):
        verify_package(package, "0" * 64)


def test_source_download_pins_repository_release_and_manifest_digest(monkeypatch) -> None:
    from opsmesh_sdk.sources import github

    payload = b"static package"
    entry = manifest_asset()
    entry["sha256"] = hashlib.sha256(payload).hexdigest()
    base = "https://github.com/example/extensions/releases/download/v1/"
    release = {
        "tag_name": "v1",
        "assets": [
            {"name": "opsmesh-release.json", "browser_download_url": base + "opsmesh-release.json"},
            {"name": "mail.whl", "browser_download_url": base + "mail.whl"},
        ],
    }
    monkeypatch.setattr(github, "github_json", lambda url: release)
    monkeypatch.setattr(
        github,
        "github_asset",
        lambda url, **kwargs: (
            json.dumps({"schema_version": 1, "packages": [entry]}).encode()
            if url.endswith("opsmesh-release.json")
            else payload
        ),
    )
    source = github.GitHubSource("example/extensions")
    downloaded = source.download("v1", "mail")
    assert downloaded.payload == payload
    assert downloaded.declaration.key == "mail"
    with pytest.raises(github.NotFoundError):
        source.release("v2")
    entry["sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        source.download("v1", "mail")


def test_source_filters_drafts_and_does_not_read_manifest_during_listing(monkeypatch) -> None:
    from opsmesh_sdk.sources import github

    monkeypatch.setattr(
        github,
        "github_json",
        lambda url: [
            {"tag_name": "v1", "assets": []},
            {"tag_name": "draft", "draft": True, "assets": []},
        ],
    )
    assert [
        release.tag_name for release in github.GitHubSource("example/extensions").list_releases()
    ] == ["v1"]


def test_package_import_uses_common_create_attach_publish_contract(
    capability_version_payload,
) -> None:
    import httpx

    from opsmesh_sdk.client import PlatformClient
    from opsmesh_sdk.sources.github import ReleasePackage
    from opsmesh_sdk.sources.packages import import_package
    from opsmesh_sdk.transport.http import HttpTransport

    payload = b"static package"
    declaration = ReleasePackage.model_validate(
        {
            **manifest_asset(),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(201 if len(requests) == 1 else 200, json=capability_version_payload)

    async def run():
        async with PlatformClient(
            "https://api.invalid",
            session_id="session",
            transport=HttpTransport(
                "https://api.invalid",
                session_id="session",
                client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            ),
        ) as channel:
            return await import_package(channel, declaration, payload, source="system")

    result = asyncio.run(run())
    assert result.version == 1
    assert [(request.method, request.url.path.rsplit("/", 1)[-1]) for request in requests] == [
        ("POST", "definitions"),
        ("PUT", "package"),
        ("POST", "publish"),
    ]
    assert json.loads(requests[0].content)["declaration"]["type"] == "extension"
    assert json.loads(requests[0].content)["source"] == "system"
    assert requests[1].content == payload
    assert requests[1].url.params["filename"] == "mail.whl"
