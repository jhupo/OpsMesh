from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.orm import Session

from opsmesh.governance.policies.service import AdminPolicyService
from opsmesh.governance.policies.worker_control import AdminWorkerPolicyControlService
from opsmesh.shared.db.session import get_db_session

if TYPE_CHECKING:
    from redis import Redis

    RedisClient = Redis[str]
else:
    RedisClient = object





def admin_policy_service(
    session: Session = Depends(get_db_session),
) -> AdminPolicyService:
    return AdminPolicyService(session)


def admin_worker_policy_service(
    session: Session = Depends(get_db_session),
) -> AdminWorkerPolicyControlService:
    return AdminWorkerPolicyControlService(session)
