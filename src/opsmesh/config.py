"""Explicit framework configuration without environment or file access."""

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field


class Env(BaseModel):
    """Runtime environment identity supplied by the host."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    environment: str = Field(default="development", pattern=r"^[a-zA-Z0-9_-]+$")


class Config(BaseModel):
    """Validated framework values supplied as a startup snapshot.

    Plugin settings and request identities are owned by their plugins.
    This model contains only the event framework behavior.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", hide_input_in_errors=True
    )

    command_start: frozenset[str] = frozenset({"/"})
    command_sep: frozenset[str] = frozenset({"."})
    session_expire_timeout: timedelta = Field(
        default=timedelta(minutes=2), gt=timedelta(0)
    )
