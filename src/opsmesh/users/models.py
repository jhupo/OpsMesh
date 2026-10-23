"""Account facts: profile, roles, status and avatar bytes."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

MAX_DISPLAY_NAME_LENGTH = 100
MAX_AVATAR_TYPE_LENGTH = 50

USER_STATUS_ACTIVE = "active"
USER_STATUS_DISABLED = "disabled"
USER_STATUS_DELETED = "deleted"
USER_STATUSES = (USER_STATUS_ACTIVE, USER_STATUS_DISABLED, USER_STATUS_DELETED)


class UserProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One account profile; the primary key is the user id everywhere.

    The profile owns platform roles; configuration grants are never stored
    or derived here. Role and
    status changes are audit facts, not a second mutable history.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(MAX_DISPLAY_NAME_LENGTH), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=USER_STATUS_ACTIVE)
    platform_roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    avatar_content: Mapped[bytes | None] = mapped_column(LargeBinary)
    avatar_content_type: Mapped[str | None] = mapped_column(String(MAX_AVATAR_TYPE_LENGTH))
    avatar_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
