from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Record:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def connect(url: str) -> sessionmaker:
    if not url.startswith("postgresql+psycopg://"):
        raise ValueError("OpsMesh requires a postgresql+psycopg database URL")
    return sessionmaker(
        create_engine(
            url,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": 10,
                "options": "-c statement_timeout=15000 -c lock_timeout=5000",
            },
        ),
        expire_on_commit=False,
    )
