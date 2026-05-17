from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.db.session import get_db_session
from backend.app.self_hosted.service import AuthenticatedWorker, SelfHostedRuntimeService

DB_SESSION_DEPENDENCY = Depends(get_db_session)


def get_authenticated_worker(
    authorization: str | None = Header(default=None, alias="X-Runtime-Authorization"),
    session: Session = DB_SESSION_DEPENDENCY,
) -> AuthenticatedWorker:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing runtime credential",
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return SelfHostedRuntimeService(session).authenticate_worker(token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
