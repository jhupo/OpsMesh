from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.infra.database import Base, Record


class CatalogPublication(Record, Base):
    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("owner_id", "name", "version"),)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(20))
    plugin_id: Mapped[str] = mapped_column(ForeignKey("plugins.id"))
    contribution: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(20), default="private")
    group_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    encrypted_config: Mapped[str] = mapped_column(Text)
    dependencies: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)
