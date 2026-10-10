from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.orm import Session

from opsmesh.platform.overview.service import AdminOverviewService
from opsmesh.shared.db.session import get_db_session

if TYPE_CHECKING:
    from redis import Redis

    RedisClient = Redis[str]
else:
    RedisClient = object





def admin_overview_service(
    session: Session = Depends(get_db_session),
) -> AdminOverviewService:
    return AdminOverviewService(session)
