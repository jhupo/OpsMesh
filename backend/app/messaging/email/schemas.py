"""Public email DTOs shared by HTTP adapters and application services.

Invitation expiry and the public URL remain here for wire/storage compatibility;
the invitation service owns token lifetime and activation-link semantics.
"""

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class MailConfiguration(BaseModel):
    enabled: bool = False
    host: str = Field(default="", max_length=253, pattern=r"^[a-zA-Z0-9.:-]*$")
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["starttls", "tls"] = "starttls"
    username: str = Field(default="", max_length=320, pattern=r"^[^\r\n]*$")
    from_email: str = Field(default="", max_length=320)
    from_name: str = Field(default="OpsMesh", max_length=120, pattern=r"^[^\r\n]*$")
    public_base_url: str = Field(default="", max_length=2048)
    invitation_expiry_hours: int = Field(default=72, ge=1, le=168)

    @field_validator("from_email")
    @classmethod
    def validate_sender(cls, value: str) -> str:
        value = value.strip().lower()
        if value and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
            raise ValueError("Invalid sender email")
        return value

    @field_validator("public_base_url")
    @classmethod
    def validate_public_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or any(char.isspace() for char in value)
            ):
                raise ValueError(
                    "Public URL must be an HTTP(S) address without credentials or query"
                )
        return value

    @model_validator(mode="after")
    def validate_enabled(self) -> "MailConfiguration":
        if self.enabled and not all([self.host, self.from_email, self.public_base_url]):
            raise ValueError("SMTP host, sender email and public URL are required")
        return self


class MailConfigurationUpdate(MailConfiguration):
    password: SecretStr | None = Field(default=None, repr=False)
    clear_password: bool = False


class MailConfigurationResponse(MailConfiguration):
    password_configured: bool = False


class EmailRecipient(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")
