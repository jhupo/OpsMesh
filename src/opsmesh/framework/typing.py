"""Shared transport contracts; live resources never belong in these models."""

from typing import Any

from pydantic import BaseModel, ConfigDict

JsonData = dict[str, Any]


class OpsMeshContract(BaseModel):
    """An immutable, validated public message or configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)
