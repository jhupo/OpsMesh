from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.orm import Session

from backend.app.runtime.instances.admin_service import AdminRuntimeService
from backend.app.shared.db.session import get_db_session

if TYPE_CHECKING:
    from redis import Redis

    RedisClient = Redis[str]
else:
    RedisClient = object





def admin_runtime_service(
    session: Session = Depends(get_db_session),
) -> AdminRuntimeService:
    return AdminRuntimeService(session)
