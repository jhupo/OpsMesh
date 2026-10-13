from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from opsmesh.governance.security_events.admin_queries import AdminSecurityEventQueryService
from opsmesh.shared.db.session import get_db_session


def admin_security_event_service(
    session: Session = Depends(get_db_session),
) -> AdminSecurityEventQueryService:
    return AdminSecurityEventQueryService(session)
