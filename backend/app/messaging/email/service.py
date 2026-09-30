import smtplib

from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.errors import DomainError
from backend.app.core.security.secrets import SecretEncryptionService
from backend.app.messaging.email.models import PlatformMailSettings
from backend.app.messaging.email.schemas import (
    MailConfigurationResponse,
    MailConfigurationUpdate,
)
from backend.app.messaging.email.smtp import send_smtp_message


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
