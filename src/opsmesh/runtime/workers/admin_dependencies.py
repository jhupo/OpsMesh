from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.orm import Session

from opsmesh.governance.policies.worker_control import AdminWorkerPolicyControlService
from opsmesh.runtime.workers.admin_service import AdminWorkerService
from opsmesh.shared.db.session import get_db_session

if TYPE_CHECKING:
    from redis import Redis

    RedisClient = Redis[str]
else:
    RedisClient = object





def admin_worker_service(
    session: Session = Depends(get_db_session),
) -> AdminWorkerService:
    return AdminWorkerService(session, AdminWorkerPolicyControlService(session))
