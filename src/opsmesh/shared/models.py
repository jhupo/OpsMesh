from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.infra.database import Base, Record


class AuditEvent(Record, Base):
    __tablename__ = "audit_events"
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    method: Mapped[str] = mapped_column(String(10))
    route: Mapped[str] = mapped_column(String(200))
    status: Mapped[int] = mapped_column()
    request_id: Mapped[str] = mapped_column(String(36), index=True)
