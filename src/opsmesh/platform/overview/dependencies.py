from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from opsmesh.platform.overview.service import AdminOverviewService
from opsmesh.shared.db.session import get_db_session


def admin_overview_service(
    session: Session = Depends(get_db_session),
) -> AdminOverviewService:
    return AdminOverviewService(session)
