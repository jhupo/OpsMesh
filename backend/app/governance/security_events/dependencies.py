from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.orm import Session

from backend.app.governance.security_events.query_service import AdminSecurityEventService
from backend.app.shared.db.session import get_db_session

if TYPE_CHECKING:
    from redis import Redis

    RedisClient = Redis[str]
else:
    RedisClient = object





def admin_security_event_service(
    session: Session = Depends(get_db_session),
) -> AdminSecurityEventService:
    return AdminSecurityEventService(session)
