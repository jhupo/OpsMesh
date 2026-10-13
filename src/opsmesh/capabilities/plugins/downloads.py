"""Native download Activities: bounded read-only transfer, atomic verified publication."""

import asyncio
from dataclasses import dataclass
from hashlib import sha256

from opsmesh_plugin_sdk.packaging.distribution import PluginCatalog, SignedPluginRelease
from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.exceptions import ApplicationError

from opsmesh.capabilities.plugins.distribution import PluginDistributionService
from opsmesh.capabilities.plugins.models import PluginDownload
from opsmesh.capabilities.plugins.transport import PluginFetchError, PluginHTTPFetcher
from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.auth.service import AuthenticationService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.authorization.errors import AuthorizationError
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.service import WorkspaceAuthorizationService
from opsmesh.runtime.temporal.activities import ActivityDependencies, authorize_execution
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.shared.errors import DomainError, PolicyDeniedError


@dataclass(frozen=True)
class _DownloadPlan:
    url: str
    allowed_hosts: list[str]
    sha256: str
    max_bytes: int


class PluginDownloadActivities:
    def __init__(self, services: ActivityDependencies, fetcher: PluginHTTPFetcher) -> None:
        self._services = services
        self._fetcher = fetcher

    @activity.defn(name="opsmesh.plugin.download")
    async def download(self, request: ExecutionRequest) -> None:
        heartbeat = asyncio.create_task(_heartbeat())
        try:
            preparation = asyncio.create_task(asyncio.to_thread(self._prepare, request))
            try:
                plan = await asyncio.shield(preparation)
            except asyncio.CancelledError:
                await preparation
                raise
            if plan is None:
                return
            # HTTPS GET is safe to repeat after worker loss. No database connection
            # or transaction stays open during transfer, DNS or signature decoding.
            content = await self._fetcher.fetch(
                plan.url, plan.allowed_hosts, max_bytes=plan.max_bytes
            )
            if sha256(content).hexdigest() != plan.sha256:
                raise PluginFetchError("checksum_mismatch")
            publication = asyncio.create_task(asyncio.to_thread(self._publish, request, content))
            try:
                await asyncio.shield(publication)
            except asyncio.CancelledError:
                await publication
                raise
        except PluginFetchError as exc:
            raise ApplicationError(
                "Plugin distribution transfer failed",
                type=exc.code,
                non_retryable=exc.code not in {"network_error", "download_timeout"},
            ) from None
        except (ValueError, DomainError, AuthorizationError):
            raise ApplicationError(
                "Plugin distribution verification or authorization was rejected",
                type="verification_rejected",
                non_retryable=True,
            ) from None
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    def _prepare(self, request: ExecutionRequest) -> _DownloadPlan | None:
        with self._services.transaction() as session:
            job = self._job(session, request)
            if job is None or job.status in {"succeeded", "failed"}:
                return None
            service = self._authorized(session, request, job)
            source = service.source(job.workspace_id, job.source_id)
            url, digest, max_bytes = source.url, source.sha256, 1_048_576
            if job.candidate_id is not None:
                candidate = service.candidate(job.workspace_id, job.candidate_id)
                if candidate.withdrawn:
                    raise PolicyDeniedError("Candidate withdrawn")
                url, digest, max_bytes = candidate.url, candidate.sha256, 262_144
            job.status, job.error_code = "fetching", None
            job.attempts = activity.info().attempt
            return _DownloadPlan(url, list(source.allowed_hosts), digest, max_bytes)

    def _publish(self, request: ExecutionRequest, content: bytes) -> None:
        with self._services.transaction() as session:
            job = self._job(session, request)
            if job is None or job.status in {"succeeded", "failed"}:
                return
            service = self._authorized(session, request, job)
            source = service.source(job.workspace_id, job.source_id)
            if job.candidate_id is None:
                service.apply_catalog(source, PluginCatalog.model_validate_json(content))
            else:
                candidate = service.candidate(job.workspace_id, job.candidate_id)
                if candidate.withdrawn:
                    raise PolicyDeniedError("Candidate withdrawn during download")
                release = SignedPluginRelease.model_validate_json(content)
                service.verify(candidate, release)
                candidate.verified_release = release.model_dump(mode="json")
            job.status, job.error_code = "succeeded", None
            service.audit(job.workspace_id, job.actor_id, "download_succeeded", job.id, {})

    @activity.defn(name="opsmesh.plugin.download_failed")
    def failed(self, request: ExecutionRequest, error_code: str) -> None:
        with self._services.transaction() as session:
            job = self._job(session, request)
            if job is None or job.status in {"succeeded", "failed"}:
                return
            job.status, job.error_code = "failed", error_code
            AuditService(session).record_system_action(
                workspace_id=job.workspace_id,
                action="plugin.download_failed",
                target_type="plugin_distribution",
                target_id=job.id,
                metadata={"error_code": error_code, "attempts": job.attempts},
                actor_id="opsmesh.plugin_download",
            )

    @staticmethod
    def _job(session: Session, request: ExecutionRequest) -> PluginDownload | None:
        if request.kind != ExecutionKind.PLUGIN_DOWNLOAD:
            raise ValueError("Plugin download requires a download execution reference")
        # Match every short product transaction's workspace -> resource lock order.
        from opsmesh.workspaces.management.models import Workspace

        session.scalar(
            select(Workspace.id).where(Workspace.id == request.workspace_id).with_for_update()
        )
        return session.scalar(
            select(PluginDownload)
            .where(
                PluginDownload.workspace_id == request.workspace_id,
                PluginDownload.id == request.resource_id,
                PluginDownload.execution_id == request.execution_id,
            )
            .with_for_update()
        )

    @staticmethod
    def _authorized(
        session: Session, request: ExecutionRequest, job: PluginDownload
    ) -> PluginDistributionService:
        if request.requested_by_user_id != job.actor_id:
            raise PolicyDeniedError("Download actor differs from the authorized intent")
        authorize_execution(session, request, action=WorkspaceAction.ADMIN)
        actor = AuthenticationService(session).refresh_authenticated_user(
            AuthenticatedUser(
                user_id=job.actor_id, email="", display_name="", token_id=job.actor_token_id
            )
        )
        WorkspaceAuthorizationService(session).require_workspace(
            user_id=actor.user_id,
            workspace_id=job.workspace_id,
            action=WorkspaceAction.ADMIN,
            authenticated_user=actor,
        )
        service = PluginDistributionService(session)
        service.authorize(job.workspace_id, job.actor_id)
        source = service.source(job.workspace_id, job.source_id)
        if not source.enabled or source.generation != job.source_generation:
            raise PolicyDeniedError("Source changed")
        return service


async def _heartbeat() -> None:
    while True:
        activity.heartbeat()
        await asyncio.sleep(5)
