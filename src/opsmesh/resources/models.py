from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.infra.database import Base, Record


class Workspace(Record, Base):
    __tablename__ = "workspaces"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))


class Artifact(Record, Base):
    __tablename__ = "artifacts"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(200))
    size: Mapped[int] = mapped_column(default=0)
