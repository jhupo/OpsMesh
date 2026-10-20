"""Persisted admission switch shared by all API and Worker processes."""

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from opsmesh.platform.operations.models import PlatformAdmission


def is_open(session: Session) -> bool:
    session.execute(text("SELECT pg_advisory_xact_lock_shared(736412891)"))
    setting = session.scalar(
        select(PlatformAdmission)
        .where(PlatformAdmission.id == 1)
        .execution_options(populate_existing=True)
    )
    return setting is None or not setting.drained


def set_drained(session: Session, *, drained: bool) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(736412891)"))
    setting = session.scalar(
        select(PlatformAdmission).where(PlatformAdmission.id == 1).with_for_update()
    )
    if setting is None:
        session.add(PlatformAdmission(id=1, drained=drained, revision=1))
    else:
        setting.drained = drained
        setting.revision += 1
    session.flush()
