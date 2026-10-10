from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base


class ExecutionContractRollback(Base):
    """Original configuration retained solely for revision 0116's downgrade."""

    __tablename__ = "execution_contract_rollback"

    table_name: Mapped[str] = mapped_column(String(80), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    column_name: Mapped[str] = mapped_column(String(80), primary_key=True)
    original: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
