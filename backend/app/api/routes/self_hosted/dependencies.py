from fastapi import Depends
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.self_hosted.dispatch import SelfHostedDispatchService
from backend.app.self_hosted.events import SelfHostedEventRecorder
from backend.app.self_hosted.job_completion import SelfHostedRunCompletionService
from backend.app.self_hosted.jobs import SelfHostedJobFinalizer
from backend.app.self_hosted.mcp_jobs import SelfHostedMcpJobService
from backend.app.self_hosted.progress import SelfHostedProgressService
from backend.app.self_hosted.service import SelfHostedRuntimeService


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
) -> SelfHostedRunCompletionService:
    return SelfHostedRunCompletionService(session)


def self_hosted_mcp_job_service(
    session: Session = Depends(get_db_session),
) -> SelfHostedMcpJobService:
    return SelfHostedMcpJobService(session)


def self_hosted_progress_service(
    session: Session = Depends(get_db_session),
) -> SelfHostedProgressService:
    return SelfHostedProgressService(session)
