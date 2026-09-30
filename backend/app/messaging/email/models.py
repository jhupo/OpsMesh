from sqlalchemy import CheckConstraint, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db.base import Base, TimestampMixin


class PlatformMailSettings(TimestampMixin, Base):
    __tablename__ = "platform_mail_settings"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    password_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    encryption_key_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
