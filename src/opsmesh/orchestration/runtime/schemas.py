"""Runtime API accepts references and sandbox values, never host paths or credentials."""

import base64
import binascii
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from opsmesh.shared.files import validate_relative_path


class EnvironmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configuration_id: UUID | None = None
    root_directory_id: UUID | None = None
    image: str = Field(min_length=1, max_length=500)


class EnvironmentView(BaseModel):
    environment_id: UUID
    scope_id: UUID
    owner_ref: str
    status: str
    error_code: str | None


def validate_file_content(value: str) -> str:
    try:
        content = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("The file content must be valid base64") from None
    if len(content) > 4_000_000:
        raise ValueError("The managed file exceeds its byte limit")
    return value


RelativePath = Annotated[str, AfterValidator(validate_relative_path)]
FileContent = Annotated[str, Field(max_length=6_000_000), AfterValidator(validate_file_content)]


class EnvironmentFileDownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: RelativePath


class EnvironmentFileUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: RelativePath
    content_base64: FileContent


class EnvironmentCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(min_length=1, max_length=100)
    timeout_seconds: int = Field(default=60, ge=1, le=3600)


class EnvironmentOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["resume", "close", "command", "read", "write"]
    command: list[str] = Field(default_factory=list, max_length=100)
    path: RelativePath | None = None
    content_base64: FileContent | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=3600)


class RuntimeOperationView(BaseModel):
    operation_id: str
    status: str
    revision: int = 0
    result: dict[str, object] | None = None


class MCPCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configuration_id: UUID | None = None
    name: str = Field(min_length=1, max_length=100)
    transport: Literal["streamable_http", "sse", "stdio"]
    endpoint: str | None = Field(default=None, max_length=2000)
    environment_id: UUID | None = None
    command: list[str] = Field(default_factory=list, max_length=100)
    environment: dict[str, str] = Field(default_factory=dict, max_length=64)

    @model_validator(mode="after")
    def environment_contract(self) -> "MCPCreateRequest":
        if self.transport != "stdio" and self.environment:
            raise ValueError("Only stdio MCP accepts environment declarations")
        reserved = {"PATH", "HOME", "USER", "SHELL", "BASH_ENV", "ENV", "IFS", "GODEBUG"}
        for name in self.environment:
            if (
                not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,99}", name)
                or name in reserved
                or name.startswith(("LD_", "DYLD_", "DOCKER_", "PYTHON", "SSL_"))
                or name.endswith("PROXY")
            ):
                raise ValueError("An MCP environment name conflicts with its host transport")
        if sum(len(value.encode()) for value in self.environment.values()) > 65536:
            raise ValueError("MCP environment values exceed their bound")
        return self


class MCPView(BaseModel):
    connection_id: UUID
    name: str
    transport: str
    status: str
    tools: list[dict[str, object]]
    tools_digest: str = ""
    error_code: str | None


class MCPToolsView(BaseModel):
    connection_id: UUID
    tools: list[dict[str, object]]
    tools_digest: str
