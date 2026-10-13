from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from opsmesh.runtime.instances.admin_service import AdminRuntimeService
from opsmesh.shared.db.session import get_db_session


def admin_runtime_service(
    session: Session = Depends(get_db_session),
) -> AdminRuntimeService:
    return AdminRuntimeService(session)
