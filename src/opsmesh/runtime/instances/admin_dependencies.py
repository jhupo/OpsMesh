from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.orm import Session

from opsmesh.runtime.instances.admin_service import AdminRuntimeService
from opsmesh.shared.db.session import get_db_session

if TYPE_CHECKING:
    from redis import Redis

    RedisClient = Redis[str]
else:
    RedisClient = object


def admin_runtime_service(
    session: Session = Depends(get_db_session),
) -> AdminRuntimeService:
    return AdminRuntimeService(session)
