from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.db.errors import DatabaseConflictError, flush_or_raise_conflict
from backend.app.core.errors import ConflictError, DomainError
from backend.app.core.utils import ensure_aware_utc
from backend.app.domains.access.models import User, UserInvitation
from backend.app.domains.access.service import AuthorizationService
from backend.app.messaging.email.service import PlatformMailService


class UserInvitationService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._mail = PlatformMailService(session, settings)

    def invite(self, *, email: str, display_name: str, platform_admin: bool) -> UserInvitation:
        self._mail.require_enabled()
        email = AuthorizationService.normalize_email(email)
        user = self._session.scalar(select(User).where(User.email == email).with_for_update())
        if user is not None:
            invitation = self._session.scalar(
                select(UserInvitation).where(UserInvitation.user_id == user.id)
            )
            if user.status == "invited" and invitation is not None:
                return invitation
            raise ConflictError("User email already exists")
        user = User(
            email=email,
            display_name=display_name.strip() or email.split("@")[0],
            status="invited",
            platform_admin=platform_admin,
        )
        self._session.add(user)
        self._flush("User email already exists")
        return self._deliver(user)

    def resend(self, user_id: UUID) -> UserInvitation:
        self._mail.require_enabled()
        user = self._session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None or user.status != "invited":
            raise ConflictError("Only pending invitations can be resent")
        return self._deliver(user)

    def _deliver(self, user: User) -> UserInvitation:
        configuration = self._mail.require_enabled()
        invitation = self._session.scalar(
            select(UserInvitation).where(UserInvitation.user_id == user.id).with_for_update()
        )
        now = datetime.now(UTC)
        if (
            invitation
            and invitation.delivery_status == "pending"
            and ensure_aware_utc(invitation.updated_at) > now - timedelta(minutes=1)
        ):
            raise ConflictError("Invitation delivery is in progress; retry in one minute")
        if (
            invitation
            and invitation.sent_at
            and ensure_aware_utc(invitation.sent_at) > now - timedelta(seconds=30)
        ):
            raise ConflictError("Wait 30 seconds before resending an invitation")
        token = token_urlsafe(32)
        if invitation is None:
            invitation = UserInvitation(user_id=user.id)
            self._session.add(invitation)
        invitation.token_hash = sha256(token.encode()).hexdigest()
        invitation.expires_at = now + timedelta(hours=configuration.invitation_expiry_hours)
        invitation.delivery_status = "pending"
        submitted_hash = invitation.token_hash
        self._session.commit()
        link = f"{configuration.public_base_url}/accept-invitation#token={token}"
        try:
            self._mail.send(
                recipient=user.email,
                subject="OpsMesh 用户邀请 / Account invitation",
                body=(
                    "您受邀加入 OpsMesh，请打开链接设置密码并激活账号：\n"
                    "You are invited to OpsMesh. Set your password to activate your account:\n\n"
                    f"{link}\n\n"
                    f"有效期 / Valid for: {configuration.invitation_expiry_hours} 小时 / hours.\n"
                    "如非本人申请，请忽略此邮件。If unexpected, ignore this email."
                ),
            )
        except DomainError:
            delivery_status = "failed"
            sent_at = invitation.sent_at
        else:
            delivery_status = "sent"
            sent_at = datetime.now(UTC)
        self._session.execute(
            update(UserInvitation)
            .where(UserInvitation.id == invitation.id, UserInvitation.token_hash == submitted_hash)
            .values(delivery_status=delivery_status, sent_at=sent_at)
        )
        self._session.commit()
        self._session.refresh(invitation)
        return invitation

    def accept(self, *, token: str, password: str, display_name: str, username: str) -> User:
        token_hash = sha256(token.encode()).hexdigest()
        invitation = self._session.scalar(
            select(UserInvitation).where(UserInvitation.token_hash == token_hash)
        )
        if invitation is None:
            raise self._invalid_invitation()
        user = self._session.scalar(
            select(User).where(User.id == invitation.user_id).with_for_update()
        )
        invitation = self._session.scalar(
            select(UserInvitation)
            .where(UserInvitation.token_hash == token_hash)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            invitation is None
            or invitation.accepted_at is not None
            or ensure_aware_utc(invitation.expires_at) <= datetime.now(UTC)
            or user is None
            or user.status != "invited"
        ):
            raise self._invalid_invitation()
        user.display_name = display_name.strip()
        user.username = username.strip() or None
        user.password_hash = AuthorizationService.hash_password(password)
        user.status = "active"
        invitation.accepted_at = datetime.now(UTC)
        self._flush("Username already exists")
        return user

    def _flush(self, message: str) -> None:
        try:
            flush_or_raise_conflict(self._session, message)
        except DatabaseConflictError as error:
            raise ConflictError(message) from error

    @staticmethod
    def _invalid_invitation() -> DomainError:
        return DomainError(
            "Invitation is invalid, expired or already accepted",
            code="invalid_invitation",
            status_code=400,
        )
