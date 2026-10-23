"""Monetary reservations use a locked user/currency/month account."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin


class SpendingLimit(TimestampMixin, Base):
    __tablename__ = "spending_limits"
    __table_args__ = (
        CheckConstraint("amount IS NULL OR amount >= 0", name="spending_limit_amount"),
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    warning_ratio: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0.8"))
    revision: Mapped[int] = mapped_column(Integer, default=1)


class SpendingAccount(TimestampMixin, Base):
    __tablename__ = "spending_accounts"
    __table_args__ = (
        CheckConstraint("spent >= 0 AND reserved >= 0", name="spending_account_amounts"),
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    spent: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal(0))
    reserved: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal(0))


class ModelCharge(TimestampMixin, Base):
    __tablename__ = "model_charges"
    __table_args__ = (
        CheckConstraint(
            "status IN ('reserved', 'settled', 'released')", name="model_charge_status"
        ),
        CheckConstraint(
            "reserved >= 0 AND (amount IS NULL OR amount >= 0)", name="model_charge_amounts"
        ),
    )
    operation_id: Mapped[str] = mapped_column(String(500), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("execution_runs.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    provider_id: Mapped[str | None] = mapped_column(String(200))
    model_id: Mapped[str | None] = mapped_column(String(200))
    currency: Mapped[str | None] = mapped_column(String(3))
    period: Mapped[str] = mapped_column(String(7))
    pricing: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    reserved: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal(0))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="reserved")
