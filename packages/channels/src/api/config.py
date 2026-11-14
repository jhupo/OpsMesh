"""API channel settings owned by the plugin, not the host."""

from __future__ import annotations

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from ..attachments import AttachmentSettings
from ..server import ListenerSettings

_ID_PATTERN = r"^[A-Za-z0-9_.:@-]{1,128}$"
_ENV_REF_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"


def _clean_identity_values(
    values: tuple[str, ...], *, field: str
) -> tuple[str, ...]:
    cleaned: list[str] = []
    for value in values:
        if not value or len(value) > 200:
            raise ValueError(f"{field} entries must be 1 to 200 characters")
        if any(ord(char) < 0x20 for char in value):
            raise ValueError(
                f"{field} entries cannot contain control characters"
            )
        if value not in cleaned:
            cleaned.append(value)
    return tuple(cleaned)


class IdentitySettings(BaseModel):
    """One trusted API token and the caller identity it establishes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=_ID_PATTERN)
    name: str | None = Field(default=None, max_length=200)
    groups: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    token_ref: str = Field(pattern=_ENV_REF_PATTERN)
    allow_invoke: bool = True
    allow_admin: bool = False

    @field_validator("groups", "roles")
    @classmethod
    def _validate_values(cls, value: tuple[str, ...], info) -> tuple[str, ...]:
        return _clean_identity_values(value, field=info.field_name)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or len(value) > 200:
            raise ValueError("name must be 1 to 200 characters")
        if any(ord(char) < 0x20 for char in value):
            raise ValueError("name cannot contain control characters")
        return value


class Settings(ListenerSettings):
    """Authentication configuration for the generic HTTP channel.

    A token is read from the environment variable named by ``token_ref``.
    Caller identifiers are namespaced to the API channel so they never
    silently merge with channel users that happen to share a display name.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    attachments: AttachmentSettings = Field(default_factory=AttachmentSettings)
    identities: tuple[IdentitySettings, ...] = Field(min_length=1)
    request_timeout_seconds: float = Field(default=300, gt=0)
    max_output_messages: int = Field(default=128, gt=0)
    max_output_bytes: int = Field(default=8 * 1024 * 1024, gt=0)
    stream_buffer_size: int = Field(default=16, gt=0, le=1024)
    stream_error_message: str = Field(
        default="stream failed", min_length=1, max_length=200
    )

    @model_validator(mode="after")
    def _unique_principals(self) -> Settings:
        ids: set[str] = set()
        refs: set[str] = set()
        for identity in self.identities:
            if identity.id in ids:
                raise ValueError(f"Duplicate API identity id: {identity.id}")
            if identity.token_ref in refs:
                raise ValueError(
                    "Each API identity requires a distinct token_ref"
                )
            ids.add(identity.id)
            refs.add(identity.token_ref)
        return self
