"""Explicitly register the ORM models owned by the current application."""

from sqlalchemy import MetaData

from opsmesh.governance.approvals.models import ApprovalRequest
from opsmesh.governance.audit.models import (
    AuditAppendReceipt,
    AuditChainHead,
    AuditEvent,
    AuditVerification,
)
from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.identity.auth.models import LoginSession, UserCredential
from opsmesh.identity.invitations.models import AccountInvitation
from opsmesh.identity.quotas.models import (
    StorageReservation,
    TokenReservation,
    UserQuotaLimit,
    UserStorageAccount,
    UserTokenAccount,
)
from opsmesh.identity.users.models import UserProfile
from opsmesh.platform.mail.models import MailIntent, SmtpConfiguration
from opsmesh.shared.db.base import Base
from opsmesh.shared.db.idempotency import CreationReceipt
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry


def register_models() -> MetaData:
    """Explicitly register the tables owned by the current application."""
    _ = (
        ApprovalRequest,
        AuditAppendReceipt,
        AuditChainHead,
        AuditEvent,
        AuditVerification,
        AccountInvitation,
        CreationReceipt,
        LoginSession,
        MailIntent,
        SecurityEvent,
        SmtpConfiguration,
        StorageReservation,
        TemporalOutboxEntry,
        TokenReservation,
        UserCredential,
        UserProfile,
        UserQuotaLimit,
        UserStorageAccount,
        UserTokenAccount,
    )
    return Base.metadata
