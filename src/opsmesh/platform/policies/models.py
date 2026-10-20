"""One current platform policy with immutable revision evidence."""

from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PlatformPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "platform_policies"
    key: Mapped[str] = mapped_column(String(100), unique=True)
    revision: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool]
    policy: Mapped[dict[str, object]] = mapped_column(JSONB)


class PlatformPolicyRevision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "platform_policy_revisions"
    __table_args__ = (UniqueConstraint("policy_id", "revision"),)
    policy_id: Mapped[UUID] = mapped_column(ForeignKey("platform_policies.id"))
    revision: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool]
    policy: Mapped[dict[str, object]] = mapped_column(JSONB)
    actor_id: Mapped[UUID]
