from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.infra.database import Base, Record


class UserQuota(Base):
    __tablename__ = "quotas"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    monthly_tokens: Mapped[int] = mapped_column(BigInteger, default=1_000_000)
    storage_bytes: Mapped[int] = mapped_column(BigInteger, default=1_000_000_000)
    concurrent_tasks: Mapped[int] = mapped_column(default=4)


class TokenBudget(Base):
    __tablename__ = "token_budgets"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    consumed: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved: Mapped[int] = mapped_column(BigInteger, default=0)


class TokenReservation(Record, Base):
    __tablename__ = "token_reservations"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    period: Mapped[str] = mapped_column(String(7))
    amount: Mapped[int] = mapped_column(BigInteger)
    actual: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="reserved")


class ModelConnection(Record, Base):
    __tablename__ = "model_connections"
    __table_args__ = (
        UniqueConstraint("owner_id", "name"),
        Index(
            "uq_model_connections_default",
            "owner_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str] = mapped_column(String(2000), default="https://api.openai.com/v1")
    api_mode: Mapped[str] = mapped_column(String(30), default="responses")
    model_name: Mapped[str | None] = mapped_column(String(200))
    reasoning_effort: Mapped[str] = mapped_column(String(20), default="medium")
    is_default: Mapped[bool] = mapped_column(default=False)
    encrypted_key: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
