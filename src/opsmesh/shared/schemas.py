"""Shared HTTP schema conventions."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ReasoningEffort = Literal["low", "medium", "high", "xhigh"]


class RequestSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResourceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorView(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class RecordView(ResourceView):
    id: str
    created_at: datetime
