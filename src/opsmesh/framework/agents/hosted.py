"""Public provider-hosted tool contracts; execution occurs at the model provider."""

from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from opsmesh.framework.typing import OpsMeshContract


class WebSearchBinding(OpsMeshContract):
    type: Literal["web_search"] = "web_search"
    search_context_size: Literal["low", "medium", "high"] = "medium"
    allowed_domains: tuple[str, ...] = ()
    external_web_access: bool = True
    country: str | None = None
    city: str | None = None
    region: str | None = None
    timezone: str | None = None


class FileSearchBinding(OpsMeshContract):
    type: Literal["file_search"] = "file_search"
    vector_store_ids: tuple[str, ...] = Field(min_length=1)
    max_num_results: int = Field(default=10, ge=1, le=50)
    include_search_results: bool = False
    filters: dict[str, Any] | None = None
    ranking_options: dict[str, Any] | None = None


class CodeInterpreterBinding(OpsMeshContract):
    type: Literal["code_interpreter"] = "code_interpreter"
    container_id: str | None = None
    file_ids: tuple[str, ...] = ()
    memory_limit: Literal["1g", "4g", "16g", "64g"] = "1g"


class ImageGenerationBinding(OpsMeshContract):
    type: Literal["image_generation"] = "image_generation"
    quality: Literal["auto", "low", "medium", "high"] = "auto"
    size: Literal["auto", "1024x1024", "1024x1536", "1536x1024"] = "auto"
    output_format: Literal["png", "jpeg", "webp"] = "png"
    background: Literal["auto", "transparent", "opaque"] = "auto"
    partial_images: int = Field(default=0, ge=0, le=3)


class HostedMCPBinding(OpsMeshContract):
    type: Literal["mcp"] = "mcp"
    server_label: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    server_url: str | None = None
    connector_id: str | None = None
    authorization_secret_ref: str | None = None
    header_secret_refs: dict[str, str] = Field(default_factory=dict)
    allowed_tools: tuple[str, ...] = Field(min_length=1)
    require_approval: Literal["always", "never"] = "always"
    approval_policy_ref: str | None = None
    description: str | None = None
    defer_loading: bool = False

    @model_validator(mode="after")
    def validate_endpoint(self) -> Self:
        if bool(self.server_url) == bool(self.connector_id):
            raise ValueError("Choose exactly one hosted MCP URL or connector")
        if self.server_url:
            from urllib.parse import urlsplit

            url = urlsplit(self.server_url)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError(
                    "Hosted MCP requires an HTTPS URL without credentials, query or fragment"
                )
        return self


class HostedMCPToolSelection(OpsMeshContract):
    """Select one explicitly allowed tool on a provider-hosted MCP server."""

    type: Literal["mcp"] = "mcp"
    server_label: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1)


class ToolSearchBinding(OpsMeshContract):
    type: Literal["tool_search"] = "tool_search"
    description: str | None = None
    execution: Literal["server"] = "server"


HostedToolBinding = Annotated[
    WebSearchBinding
    | FileSearchBinding
    | CodeInterpreterBinding
    | ImageGenerationBinding
    | HostedMCPBinding
    | ToolSearchBinding,
    Field(discriminator="type"),
]
