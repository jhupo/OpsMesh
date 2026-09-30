import re
import smtplib
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.errors import DomainError
from backend.app.core.mail import send_smtp_message
from backend.app.core.security.secrets import SecretEncryptionService
from backend.app.domains.platform.admin.models import PlatformMailSettings


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


class PlatformMailService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._encryption = SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
            previous_secrets=settings.credential_encryption_previous_secrets,
        )

    def configuration(self) -> MailConfigurationResponse:
        record = self._session.get(PlatformMailSettings, 1)
        return MailConfigurationResponse(
            **(record.configuration if record else {}),
            password_configured=bool(record and record.password_ciphertext),
        )

    def save(self, payload: MailConfigurationUpdate) -> MailConfigurationResponse:
        record = self._session.get(PlatformMailSettings, 1, with_for_update=True)
        if record is None:
            record = PlatformMailSettings(id=1)
            self._session.add(record)
        password = payload.password.get_secret_value() if payload.password else ""
        if payload.clear_password and password:
            raise DomainError("Cannot set and clear the SMTP password together")
        if payload.clear_password:
            record.password_ciphertext = None
            record.encryption_key_id = None
        elif password:
            encrypted = self._encryption.encrypt_payload({"password": password})
            record.password_ciphertext = encrypted.ciphertext
            record.encryption_key_id = encrypted.key_id
        if payload.enabled and payload.username and not record.password_ciphertext:
            raise DomainError(
                "SMTP authentication requires a password", code="mail_password_required"
            )
        record.configuration = payload.model_dump(exclude={"password", "clear_password"})
        self._session.flush()
        return self.configuration()

    def require_enabled(self) -> MailConfigurationResponse:
        configuration = self.configuration()
        if not configuration.enabled:
            raise DomainError(
                "Configure and enable email in system settings first",
                code="mail_not_configured",
                status_code=409,
            )
        return configuration

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        configuration = self.require_enabled()
        record = self._session.get(PlatformMailSettings, 1)
        try:
            password = ""
            if record and record.password_ciphertext:
                secret = self._encryption.decrypt_payload(
                    record.password_ciphertext, key_id=record.encryption_key_id
                )
                password = str(secret.get("password", ""))
            send_smtp_message(
                host=configuration.host,
                port=configuration.port,
                security=configuration.security,
                username=configuration.username,
                password=password,
                from_email=configuration.from_email,
                from_name=configuration.from_name,
                recipient=recipient,
                subject=subject,
                body=body,
            )
        except (OSError, smtplib.SMTPException, ValueError):
            raise DomainError(
                "Email delivery failed; check SMTP settings and retry",
                code="mail_delivery_failed",
                status_code=502,
            ) from None
