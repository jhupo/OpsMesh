"""Neutral value types shared by clients, extensions and generated DTOs."""

from __future__ import annotations

import re
from typing import Annotated, Final, NewType

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TRACEPARENT_PATTERN: Final = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


class ResourceRef(BaseModel):
    """A reference to a platform resource owned by another module.

    Only the stable identity travels; the owner's ORM rows never do.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_type: str = Field(min_length=1, max_length=100)
    resource_id: str = Field(min_length=1, max_length=128)
    version: str | None = Field(default=None, max_length=100)

    @field_validator("resource_type", "resource_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _ID_PATTERN.match(value):
            raise ValueError("identifiers must be URL-safe tokens")
        return value


class ScopeRef(BaseModel):
    """The tenant/project scope a call acts in; never forged by the caller."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: str | None = Field(default=None, max_length=128)
    project_id: str | None = Field(default=None, max_length=128)


class Cursor(BaseModel):
    """An opaque pagination cursor; the server owns its content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str = Field(min_length=1, max_length=512)


class Page(BaseModel):
    """One page of results with an optional continuation cursor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[JsonValue, ...] = ()
    next_cursor: Cursor | None = None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


class ContentBlock(BaseModel):
    """One neutral content block; prompts never travel as raw strings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    block_type: str = Field(min_length=1, max_length=50)
    text: str | None = None
    ref: ResourceRef | None = None
    media_type: str | None = Field(default=None, max_length=100)


class Usage(BaseModel):
    """Reported resource consumption; billing facts stay with their owner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    requests: int = Field(default=0, ge=0)
    units: dict[str, int] = Field(default_factory=dict)


IdempotencyKey = NewType("IdempotencyKey", Annotated[str, Field(min_length=8, max_length=200)])


class TraceContext(BaseModel):
    """W3C trace context propagated across transport boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    traceparent: str = Field(pattern=_TRACEPARENT_PATTERN.pattern)
    tracestate: str | None = Field(default=None, max_length=512)


class FlowRunRef(BaseModel):
    """A reference to a flow run started through host.jobs or orchestration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    flow_run_id: str = Field(min_length=1, max_length=128)
    definition_revision: str | None = Field(default=None, max_length=100)
