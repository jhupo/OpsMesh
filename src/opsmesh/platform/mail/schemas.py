"""SMTP configuration, send and receipt HTTP contracts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from opsmesh.platform.mail.service import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_SUBJECT_LENGTH,
)

MAX_RECIPIENT_LENGTH = 320
MAX_TEST_NOTE_LENGTH = 2000


class SmtpConfigurationView(BaseModel):
    """The stored configuration; the password is never rendered back."""

    model_config = ConfigDict(extra="forbid")

    host: str
    port: int
    security: str
    username: str
    from_email: EmailStr
    from_name: str
    timeout_seconds: int
    password_configured: bool
    updated_at: datetime | None


class SmtpConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    security: str = Field(max_length=20)
    username: str = Field(default="", max_length=255)
    password: str = Field(min_length=1, max_length=255)
    from_email: EmailStr = Field(max_length=MAX_RECIPIENT_LENGTH)
    from_name: str = Field(min_length=1, max_length=100)
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class TestMailRequest(BaseModel):
    """One administrator-triggered SMTP test.

    ``idempotency_key`` identifies the test operation, not the configuration:
    a retry of the same request reuses the key and replays its intent, while
    a new test — after a failure, a configuration change or simply again —
    carries a new key and starts a new send.
    """

    model_config = ConfigDict(extra="forbid")

    recipient: EmailStr = Field(max_length=MAX_RECIPIENT_LENGTH)
    idempotency_key: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LENGTH)


class SendMailRequest(BaseModel):
    """One delegated send; the source comes from the verified invocation."""

    model_config = ConfigDict(extra="forbid")

    recipient: EmailStr = Field(max_length=MAX_RECIPIENT_LENGTH)
    subject: str = Field(min_length=1, max_length=MAX_SUBJECT_LENGTH)
    body: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LENGTH)
    source_ref: str | None = Field(default=None, max_length=200)


class MailIntentView(BaseModel):
    """One durable send intent and its confirmed or unconfirmed outcome."""

    model_config = ConfigDict(extra="forbid")

    intent_id: UUID
    source: str
    source_ref: str | None
    source_revision: str | None
    recipient: str
    subject: str
    status: str
    attempts: int
    outcome_unknown: bool
    failure_reason: str | None
    created_at: datetime
    sent_at: datetime | None
    reconciled_at: datetime | None
    reconciliation_note: str | None


class MailIntentPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intents: list[MailIntentView]
    total: int


class ReconcileMailRequest(BaseModel):
    """The operator's confirmation of one unconfirmed send."""

    model_config = ConfigDict(extra="forbid")

    outcome: str = Field(max_length=20)
    note: str | None = Field(default=None, max_length=MAX_TEST_NOTE_LENGTH)
