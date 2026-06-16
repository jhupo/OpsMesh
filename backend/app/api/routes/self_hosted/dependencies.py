from fastapi import Depends
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.self_hosted.service import SelfHostedRuntimeService


def self_hosted_service(
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> SelfHostedRuntimeService:
    return SelfHostedRuntimeService(session, settings)
