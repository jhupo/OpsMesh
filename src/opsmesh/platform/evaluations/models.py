"""Immutable dataset versions and durable experiment case receipts."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EvaluationDataset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_datasets"
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)


class DatasetVersion(TimestampMixin, Base):
    __tablename__ = "evaluation_dataset_versions"
    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("evaluation_datasets.id"), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    encrypted_spec: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))


class EvaluationExperiment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_experiments"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "request_key"),
        CheckConstraint(
            "status IN ('queued', 'running', 'needs_attention', 'cancelling', "
            "'completed', 'cancelled', 'failed')",
            name="evaluation_experiment_status",
        ),
    )
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("evaluation_datasets.id"), index=True)
    dataset_version: Mapped[int] = mapped_column(Integer)
    request_key: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    encrypted_spec: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))


class EvaluationCase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_cases"
    __table_args__ = (UniqueConstraint("experiment_id", "case_id", "target_id"),)
    experiment_id: Mapped[UUID] = mapped_column(ForeignKey("evaluation_experiments.id"), index=True)
    case_id: Mapped[str] = mapped_column(String(120))
    target_id: Mapped[UUID]
    target_kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    run_id: Mapped[UUID | None]
    encrypted_result: Mapped[str | None] = mapped_column(Text)
    key_id: Mapped[str | None] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(100))


class RunFeedback(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "run_feedback"
    __table_args__ = (UniqueConstraint("owner_user_id", "target_kind", "run_id"),)
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    target_kind: Mapped[str] = mapped_column(String(16))
    run_id: Mapped[UUID]
    rating: Mapped[int] = mapped_column(Integer)
    encrypted_payload: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))
