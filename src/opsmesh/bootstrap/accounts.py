"""Provision execution accounts in the same transaction as a new identity."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from secrets import token_urlsafe
from uuid import UUID

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from opsmesh.config import Settings
from opsmesh.identity.auth.models import LoginSession, UserCredential
from opsmesh.identity.auth.passwords import hash_password
from opsmesh.identity.provisioning import AccountDefaults, provision_first_admin
from opsmesh.identity.quotas.service import ensure_capacity_accounts
from opsmesh.identity.users.models import UserProfile
from opsmesh.shared.audit import AuditActor, AuditRecord, record_audit
from opsmesh.shared.errors import ConflictError


@dataclass(frozen=True)
class AccountResources:
    settings: Settings

    def __call__(self, session: Session, user_id: UUID) -> None:
        ensure_capacity_accounts(
            session,
            user_id=user_id,
            limits={
                "concurrency": self.settings.default_user_concurrency_limit,
                "environment": self.settings.default_user_environment_limit,
            },
        )


def administrator_handoff(
    session: Session,
    *,
    reset: bool,
    email: str,
    defaults: AccountDefaults,
    provision_resources: Callable[[Session, UUID], None],
) -> dict[str, str]:
    """An explicit host-owner command hands a fresh password to the terminal once."""
    email = str(TypeAdapter(EmailStr).validate_python(email))
    session.execute(text("SELECT pg_advisory_xact_lock(6754457803939304980)"))
    profile = session.scalar(
        select(UserProfile).where(UserProfile.email == email).with_for_update()
    )
    if not reset and profile is not None and "admin" in profile.platform_roles:
        return {
            "user_id": str(profile.id),
            "email": profile.email,
            "status": "already_initialized",
            "message": "Use reset-admin-password for an explicit password reset.",
        }
    password = token_urlsafe(24)
    if reset:
        if profile is None or "admin" not in profile.platform_roles or profile.status != "active":
            raise ConflictError("The requested active administrator does not exist")
        credential = session.scalar(
            select(UserCredential).where(UserCredential.user_id == profile.id).with_for_update()
        )
        if credential is None:
            raise ConflictError("The administrator has no password credential")
        credential.password_hash = hash_password(password)
        now = datetime.now(UTC)
        for login in session.scalars(
            select(LoginSession)
            .where(LoginSession.user_id == profile.id, LoginSession.revoked_at.is_(None))
            .with_for_update()
        ):
            login.revoked_at = now
        record_audit(
            session,
            AuditRecord(
                category="security",
                action="administrator.password_reset",
                actor=AuditActor(actor_type="platform"),
                source="platform",
                target_type="user",
                target_id=str(profile.id),
                summary="Administrator password reset by an explicit host command",
            ),
        )
    else:
        if session.scalar(select(func.count()).select_from(UserProfile)):
            raise ConflictError("The platform already has accounts")
        profile = provision_first_admin(
            session,
            email=email,
            display_name="Administrator",
            password=password,
            defaults=defaults,
            provision_resources=provision_resources,
        )
    return {
        "user_id": str(profile.id),
        "email": profile.email,
        "password": password,
        "status": "password_reset" if reset else "created",
        "message": "Store this password securely; it is shown only by this command.",
    }
