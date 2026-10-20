"""Read and verify static capability packages from configured GitHub Releases."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote, unquote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from opsmesh_sdk.errors import NotFoundError, TransportError, ValidationError

MAX_RELEASE_METADATA_BYTES = 1_048_576
MAX_RELEASE_PACKAGE_BYTES = 32 * 1024 * 1024
MANIFEST_ASSET = "opsmesh-release.json"
ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


class ReleasePackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["tool", "mcp", "skill", "extension", "channel"]
    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    visibility: Literal["public", "private"] = "public"
    configuration: dict[str, object] | None = None
    resource_manifest: list[dict[str, object]] = Field(default_factory=list, max_length=512)
    dependencies: list[dict[str, object]] = Field(default_factory=list, max_length=128)
    flow_templates: list[dict[str, object]] = Field(default_factory=list, max_length=512)
    asset: str = Field(min_length=1, max_length=260)
    sha256: str = Field(pattern="^[a-f0-9]{64}$")

    @field_validator("asset")
    @classmethod
    def validate_asset_name(cls, value: str) -> str:
        if value in {".", "..", MANIFEST_ASSET} or "/" in value or "\\" in value:
            raise ValueError("Release assets must use a unique plain filename")
        return value


class ReleaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    packages: list[ReleasePackage] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_unique_packages(self) -> ReleaseManifest:
        keys = [item.key for item in self.packages]
        assets = [item.asset for item in self.packages]
        if len(set(keys)) != len(keys) or len(set(assets)) != len(assets):
            raise ValueError("Release package keys and asset names must be globally unique")
        return self


class ReleaseAsset(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    browser_download_url: str


class GitHubRelease(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    tag_name: str
    name: str | None = None
    draft: bool = False
    prerelease: bool = False
    assets: list[ReleaseAsset]

    @model_validator(mode="after")
    def unique_assets(self) -> GitHubRelease:
        names = [item.name for item in self.assets]
        if len(set(names)) != len(names):
            raise ValueError("Release asset names must be unique")
        return self


def configured_repository(value: str | None) -> str:
    if (
        value is None
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value)
        or any(part in {".", ".."} for part in value.split("/"))
    ):
        raise ValidationError("The Extension Center repository is not configured")
    return value


def github_json(url: str, *, maximum_bytes: int = MAX_RELEASE_METADATA_BYTES) -> object:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname != "api.github.com"
        or parts.port not in {None, 443}
        or parts.username
        or parts.password
    ):
        raise ValidationError("The release API URL is not an approved GitHub HTTPS URL")
    try:
        with (
            httpx.Client(timeout=httpx.Timeout(10.0, connect=3.0), trust_env=False) as client,
            client.stream(
                "GET", url, headers={"Accept": "application/vnd.github+json"}
            ) as response,
        ):
            response.raise_for_status()
            payload = _read_bounded(response.iter_bytes(chunk_size=65_536), maximum_bytes)
    except httpx.HTTPError as exc:
        raise TransportError("The configured Extension Center could not be reached") from exc
    try:
        return json.loads(payload)
    except ValueError as exc:
        raise ValidationError("GitHub returned invalid release JSON") from exc


def github_asset(url: str, *, maximum_bytes: int = MAX_RELEASE_PACKAGE_BYTES) -> bytes:
    current = url
    with httpx.Client(timeout=httpx.Timeout(30.0, connect=3.0), trust_env=False) as client:
        for _ in range(4):
            parts = urlsplit(current)
            if (
                parts.scheme != "https"
                or parts.hostname not in ALLOWED_DOWNLOAD_HOSTS
                or parts.port not in {None, 443}
                or parts.username
                or parts.password
            ):
                raise ValidationError("The release asset URL is outside the GitHub allowlist")
            try:
                with client.stream("GET", current, follow_redirects=False) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ValidationError("GitHub returned an invalid asset redirect")
                        current = str(response.url.join(location))
                        continue
                    response.raise_for_status()
                    return _read_bounded(response.iter_bytes(chunk_size=65_536), maximum_bytes)
            except httpx.HTTPError as exc:
                raise TransportError("The Extension Center asset could not be downloaded") from exc
    raise ValidationError("The release asset exceeded the redirect limit")


def _read_bounded(chunks: Iterable[bytes], maximum: int) -> bytes:
    if maximum < 1 or maximum > MAX_RELEASE_PACKAGE_BYTES:
        raise ValidationError("The GitHub response limit must be within the package size limit")
    output = bytearray()
    for chunk in chunks:
        if len(output) + len(chunk) > maximum:
            raise ValidationError("The GitHub response exceeds the configured size limit")
        output.extend(chunk)
    return bytes(output)


def parse_manifest(payload: bytes) -> ReleaseManifest:
    if len(payload) > MAX_RELEASE_METADATA_BYTES:
        raise ValidationError("The Extension Center manifest exceeds the size limit")
    try:
        return ReleaseManifest.model_validate_json(payload)
    except ValueError as exc:
        raise ValidationError("The Extension Center release manifest is invalid") from exc


@dataclass(frozen=True)
class DownloadedPackage:
    declaration: ReleasePackage
    payload: bytes


def asset_url(asset: ReleaseAsset, *, repository: str, tag: str) -> str:
    parts = urlsplit(asset.browser_download_url)
    if (
        parts.scheme != "https"
        or parts.hostname != "github.com"
        or parts.port not in {None, 443}
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or unquote(parts.path) != f"/{repository}/releases/download/{tag}/{asset.name}"
    ):
        raise ValidationError("The release asset is outside the configured repository and tag")
    return asset.browser_download_url


class GitHubSource:
    """An explicit repository source; it never receives platform credentials or installs code."""

    def __init__(self, repository: str) -> None:
        self.repository = configured_repository(repository)

    def list_releases(self) -> list[GitHubRelease]:
        payload = github_json(
            f"https://api.github.com/repos/{self.repository}/releases?per_page=100"
        )
        if not isinstance(payload, list):
            raise ValidationError("GitHub returned an invalid release collection")
        releases = [GitHubRelease.model_validate(item) for item in payload]
        return [release for release in releases if not release.draft]

    def release(self, tag: str) -> GitHubRelease:
        if not tag or len(tag) > 256:
            raise ValidationError("A release tag must contain between 1 and 256 characters")
        payload = github_json(
            f"https://api.github.com/repos/{self.repository}/releases/tags/{quote(tag, safe='')}"
        )
        release = GitHubRelease.model_validate(payload)
        if release.draft or release.tag_name != tag:
            raise NotFoundError("The requested published release is unavailable")
        return release

    def _manifest(self, release: GitHubRelease) -> ReleaseManifest:
        asset = next((item for item in release.assets if item.name == MANIFEST_ASSET), None)
        if asset is None:
            raise NotFoundError("The GitHub Release has no Extension Center manifest")
        payload = github_asset(
            asset_url(asset, repository=self.repository, tag=release.tag_name),
            maximum_bytes=MAX_RELEASE_METADATA_BYTES,
        )
        return parse_manifest(payload)

    def manifest(self, tag: str) -> ReleaseManifest:
        return self._manifest(self.release(tag))

    def download(self, tag: str, package_key: str) -> DownloadedPackage:
        from opsmesh_sdk.sources.packages import verify_package

        release = self.release(tag)
        manifest = self._manifest(release)
        package = next((entry for entry in manifest.packages if entry.key == package_key), None)
        if package is None:
            raise NotFoundError("The release package is not declared")
        asset = next((entry for entry in release.assets if entry.name == package.asset), None)
        if asset is None:
            raise NotFoundError("The declared release package asset is missing")
        payload = github_asset(asset_url(asset, repository=self.repository, tag=tag))
        verify_package(payload, package.sha256)
        return DownloadedPackage(declaration=package, payload=payload)
