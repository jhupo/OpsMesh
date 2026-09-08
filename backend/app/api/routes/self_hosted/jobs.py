from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from backend.app.api.routes.self_hosted.dependencies import (
    self_hosted_dispatch_service,
    self_hosted_progress_service,
    self_hosted_project_file_service,
    self_hosted_run_completion_service,
)
from backend.app.api.schemas.files import ArtifactResponse
from backend.app.api.schemas.runs import RunEventResponse
from backend.app.api.schemas.self_hosted import (
    JobClaimResponse,
    JobCompleteRequest,
    JobCompleteResponse,
    ProgressEventRequest,
    SelfHostedJobResponse,
    SelfHostedProjectContractResponse,
)
from backend.app.projects.runtime_io_errors import ProjectRunIOError
from backend.app.self_hosted.dependencies import get_authenticated_worker
from backend.app.self_hosted.dispatch import SelfHostedDispatchService
from backend.app.self_hosted.job_completion import SelfHostedRunCompletionService
from backend.app.self_hosted.progress import SelfHostedProgressService
from backend.app.self_hosted.project_files import (
    SelfHostedProjectContract,
    SelfHostedProjectFileService,
)
from backend.app.self_hosted.types import AuthenticatedWorker

router = APIRouter()


@router.get("/self-hosted/jobs/next", response_model=SelfHostedJobResponse | None)
async def poll_job(
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    service: SelfHostedDispatchService = Depends(self_hosted_dispatch_service),
) -> SelfHostedJobResponse | None:
    try:
        run = service.poll_job(auth)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if run is None:
        return None
    return SelfHostedJobResponse(
        agent_run_id=run.id,
        task_id=run.task_id,
        input=run.input,
        model=run.model,
        created_at=run.created_at,
    )


@router.post("/self-hosted/jobs/{agent_run_id}/claim", response_model=JobClaimResponse)
async def claim_job(
    agent_run_id: UUID,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    service: SelfHostedDispatchService = Depends(self_hosted_dispatch_service),
    project_files: SelfHostedProjectFileService = Depends(self_hosted_project_file_service),
) -> JobClaimResponse:
    try:
        claim = service.claim_job(auth, agent_run_id)
    except ValueError as exc:
        status_code = (
            status.HTTP_400_BAD_REQUEST
            if "disabled by platform safety policy" in str(exc)
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    try:
        project = project_files.describe_claimed_project(auth, agent_run_id)
    except (ValueError, ProjectRunIOError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return JobClaimResponse(
        claim_id=claim.id,
        agent_run_id=claim.agent_run_id,
        status=claim.status,
        claimed_at=claim.claimed_at,
        project=_project_contract_response(agent_run_id, project),
    )


@router.get("/self-hosted/jobs/{agent_run_id}/project/archive")
async def download_project_archive(
    agent_run_id: UUID,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    service: SelfHostedProjectFileService = Depends(self_hosted_project_file_service),
) -> Response:
    try:
        archive = service.build_input_archive(auth, agent_run_id)
    except (ValueError, ProjectRunIOError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if archive is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run has no project snapshot",
        )
    return Response(
        content=archive.content,
        media_type="application/x-tar",
        headers={
            "Content-Disposition": f'attachment; filename="project-{agent_run_id}.tar"',
            "X-OpsMesh-Project-Fingerprint": archive.fingerprint_sha256,
            "X-OpsMesh-Project-Root": archive.root_path,
        },
    )


@router.put(
    "/self-hosted/jobs/{agent_run_id}/project/outputs/{project_output_id}",
    response_model=ArtifactResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_project_output(
    agent_run_id: UUID,
    project_output_id: UUID,
    request: Request,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    service: SelfHostedProjectFileService = Depends(self_hosted_project_file_service),
) -> ArtifactResponse:
    try:
        max_bytes = service.output_limit(auth, agent_run_id, project_output_id)
        content = await _read_body_limited(request, max_bytes=max_bytes)
        media_type = request.headers.get("content-type")
        if media_type is not None:
            media_type = media_type.partition(";")[0].strip().lower() or None
        artifact = service.upload_output(
            auth,
            agent_run_id,
            project_output_id,
            content=content,
            content_type=media_type,
        )
    except (ValueError, ProjectRunIOError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ArtifactResponse.model_validate(artifact)


@router.post("/self-hosted/jobs/{agent_run_id}/complete", response_model=JobCompleteResponse)
async def complete_job(
    agent_run_id: UUID,
    request: JobCompleteRequest,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    service: SelfHostedRunCompletionService = Depends(self_hosted_run_completion_service),
) -> JobCompleteResponse:
    try:
        claim = service.complete_job(auth, agent_run_id, request)
    except (ValueError, ProjectRunIOError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if claim.completed_at is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Self-hosted job completion failed",
        )
    return JobCompleteResponse(
        claim_id=claim.id,
        agent_run_id=claim.agent_run_id,
        status=claim.status,
        completed_at=claim.completed_at,
    )


@router.post("/self-hosted/progress", response_model=RunEventResponse)
async def upload_progress(
    request: ProgressEventRequest,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    service: SelfHostedProgressService = Depends(self_hosted_progress_service),
) -> RunEventResponse:
    try:
        event = service.upload_progress(auth, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return RunEventResponse.model_validate(event)


def _project_contract_response(
    agent_run_id: UUID,
    contract: SelfHostedProjectContract | None,
) -> SelfHostedProjectContractResponse | None:
    if contract is None:
        return None
    return SelfHostedProjectContractResponse(
        root_path=contract.root_path,
        snapshot_id=contract.snapshot_id,
        fingerprint_sha256=contract.fingerprint_sha256,
        manifest=contract.manifest,
        archive_path=f"/self-hosted/jobs/{agent_run_id}/project/archive",
        output_upload_path_template=(
            f"/self-hosted/jobs/{agent_run_id}/project/outputs/{{project_output_id}}"
        ),
    )


async def _read_body_limited(request: Request, *, max_bytes: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="Project output exceeds its declared size limit",
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Content-Length must be an integer",
            ) from exc
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Project output exceeds its declared size limit",
            )
    return bytes(content)
