from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from opsmesh.runtime.self_hosted.contracts import AuthenticatedWorker
from opsmesh.runtime.self_hosted.dispatch.completion import SelfHostedRunCompletionService
from opsmesh.runtime.self_hosted.dispatch.jobs import SelfHostedJobFinalizer
from opsmesh.runtime.self_hosted.dispatch.mcp import SelfHostedMcpJobService
from opsmesh.runtime.self_hosted.dispatch.service import SelfHostedDispatchService
from opsmesh.runtime.self_hosted.projects.files import SelfHostedProjectFileService
from opsmesh.runtime.self_hosted.service import SelfHostedRuntimeService
from opsmesh.runtime.self_hosted.worker.events import SelfHostedEventRecorder
from opsmesh.runtime.self_hosted.worker.progress import SelfHostedProgressService
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session


def get_authenticated_worker(
    authorization: str | None = Header(default=None, alias="X-Runtime-Authorization"),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
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


def self_hosted_service(
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> SelfHostedRuntimeService:
    return SelfHostedRuntimeService(session, settings)


def self_hosted_dispatch_service(
    session: Session = Depends(get_db_session),
) -> SelfHostedDispatchService:
    events = SelfHostedEventRecorder(session)
    return SelfHostedDispatchService(session, events, SelfHostedJobFinalizer(session, events))


def self_hosted_run_completion_service(
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> SelfHostedRunCompletionService:
    return SelfHostedRunCompletionService(session, settings)


def self_hosted_project_file_service(
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> SelfHostedProjectFileService:
    return SelfHostedProjectFileService(session, settings)


def self_hosted_mcp_job_service(
    session: Session = Depends(get_db_session),
) -> SelfHostedMcpJobService:
    return SelfHostedMcpJobService(session)


def self_hosted_progress_service(
    session: Session = Depends(get_db_session),
) -> SelfHostedProgressService:
    return SelfHostedProgressService(session)
