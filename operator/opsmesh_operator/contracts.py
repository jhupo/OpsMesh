from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

TAG_PATTERN = r"^v[0-9]+\.[0-9]+\.[0-9]+(?:rc[0-9]+)?$"
DIGEST_PATTERN = r"^sha256:[a-f0-9]{64}$"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReleaseFile(Contract):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(gt=0, le=2_000_000_000)


class ReleaseManifest(Contract):
    schema_version: Literal[1] = 1
    tag: str = Field(pattern=TAG_PATTERN)
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    backend_digest: str = Field(pattern=DIGEST_PATTERN)
    runtime_digest: str = Field(pattern=DIGEST_PATTERN)
    database_revision: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    upgrade_from_revisions: list[str]
    rollback_database_revisions: list[str]
    connector_protocol: int = Field(ge=1)
    platforms: list[Literal["linux/amd64", "linux/arm64"]] = Field(min_length=1)
    files: list[ReleaseFile] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_files(self) -> Self:
        if len({file.name for file in self.files}) != len(self.files):
            raise ValueError("Release file names must be unique")
        return self

    def image(self, kind: Literal["backend", "runtime"]) -> str:
        owner = self.repository.split("/")[0].lower()
        name = "opsmesh" if kind == "backend" else "opsmesh-runtime"
        digest = self.backend_digest if kind == "backend" else self.runtime_digest
        return f"ghcr.io/{owner}/{name}@{digest}"


def require_tag(tag: str) -> str:
    if re.fullmatch(TAG_PATTERN, tag) is None:
        raise ValueError("Expected canonical release tag vMAJOR.MINOR.PATCH[rcN]")
    return tag
