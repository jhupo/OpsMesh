from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.core.common.config import Settings, get_settings
from backend.app.core.db.session import get_db_session
from backend.app.runtime.self_hosted.contracts import AuthenticatedWorker
from backend.app.runtime.self_hosted.service import SelfHostedRuntimeService

DB_SESSION_DEPENDENCY = Depends(get_db_session)
SETTINGS_DEPENDENCY = Depends(get_settings)


def get_authenticated_worker(
    authorization: str | None = Header(default=None, alias="X-Runtime-Authorization"),
    session: Session = DB_SESSION_DEPENDENCY,
    settings: Settings = SETTINGS_DEPENDENCY,
) -> AuthenticatedWorker:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing runtime credential",
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return SelfHostedRuntimeService(session, settings).authenticate_worker(token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
