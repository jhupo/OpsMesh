"""Durable download inbox. Network I/O never holds a database transaction."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from opsmesh_plugin_sdk.distribution import PluginCatalog, SignedPluginRelease
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError, PolicyDeniedError
from backend.app.domains.capabilities.plugins.distribution import PluginDistributionService
from backend.app.domains.capabilities.plugins.models import PluginDownload
from backend.app.domains.capabilities.plugins.transport import PluginFetchError, PluginHttpFetcher
from backend.app.observability.audit.service import AuditService


@dataclass(frozen=True)
class DownloadLease:
    workspace_id: UUID
    job_id: UUID
    token: UUID


class PluginDownloadWorker:
    def __init__(
        self, session_factory: Callable[[], Session], fetcher: PluginHttpFetcher | None = None
    ) -> None:
        self.session_factory = session_factory
        self.fetcher = fetcher or PluginHttpFetcher()

    def run_once(self) -> bool:
        lease = self._claim()
        if lease is None:
            return False
        try:
            with self.session_factory() as session:
                service = PluginDistributionService(session)
                job = service.job(lease.workspace_id, lease.job_id)
                service.authorize(job.workspace_id, job.actor_id)
                source = service.source(job.workspace_id, job.source_id)
                if not source.enabled or source.generation != job.source_generation:
                    raise PolicyDeniedError("Source changed")
                hosts = list(source.allowed_hosts)
                url, digest = source.url, source.sha256
                max_bytes = 1_048_576
                if job.candidate_id is not None:
                    candidate = service.candidate(job.workspace_id, job.candidate_id)
                    if candidate.withdrawn:
                        raise PolicyDeniedError("Candidate withdrawn")
                    url, digest = candidate.url, candidate.sha256
                    max_bytes = 262_144
            content = self.fetcher.fetch(url, hosts, max_bytes=max_bytes)
            if sha256(content).hexdigest() != digest:
                raise PluginFetchError("checksum_mismatch")
            self._finish(lease, content)
        except PluginFetchError as exc:
            self._fail(lease, exc.code, retry=exc.code in {"network_error", "download_timeout"})
        except (ValueError, DomainError):
            self._fail(lease, "verification_rejected", retry=False)
        return True

    def _claim(self) -> DownloadLease | None:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            job = session.scalar(
                select(PluginDownload)
                .where(
                    or_(
                        and_(
                            PluginDownload.status == "queued",
                            or_(
                                PluginDownload.lease_until.is_(None),
                                PluginDownload.lease_until <= now,
                            ),
                        ),
                        and_(
                            PluginDownload.status == "fetching", PluginDownload.lease_until <= now
                        ),
                    )
                )
                .order_by(PluginDownload.created_at, PluginDownload.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            token = uuid4()
            job.status, job.lease_token = "fetching", token
            job.lease_until = now + timedelta(minutes=2)
            job.attempts += 1
            attempts = job.attempts
            lease = DownloadLease(job.workspace_id, job.id, token)
            session.commit()
        if attempts > 3:
            self._fail(lease, "recovery_exhausted", retry=False)
            return None
        return lease

    def _finish(self, lease: DownloadLease, content: bytes) -> None:
        with self.session_factory() as session:
            service = PluginDistributionService(session)
            actor_id = service.job(lease.workspace_id, lease.job_id).actor_id
            service.authorize(lease.workspace_id, actor_id)
            # Lock ordering matches API mutation: workspace, then job/source resources.
            job = session.scalar(
                select(PluginDownload)
                .where(
                    PluginDownload.workspace_id == lease.workspace_id,
                    PluginDownload.id == lease.job_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if job is None or job.status != "fetching" or job.lease_token != lease.token:
                return
            source = service.source(job.workspace_id, job.source_id)
            if not source.enabled or source.generation != job.source_generation:
                raise PolicyDeniedError("Source changed during download")
            if job.candidate_id is None:
                service.apply_catalog(source, PluginCatalog.model_validate_json(content))
            else:
                candidate = service.candidate(job.workspace_id, job.candidate_id)
                if candidate.withdrawn:
                    raise PolicyDeniedError("Candidate withdrawn during download")
                release = SignedPluginRelease.model_validate_json(content)
                service.verify(candidate, release)
                candidate.verified_release = release.model_dump(mode="json")
            job.status, job.error_code, job.lease_token, job.lease_until = (
                "succeeded",
                None,
                None,
                None,
            )
            service.audit(job.workspace_id, job.actor_id, "download_succeeded", job.id, {})
            session.commit()

    def _fail(self, lease: DownloadLease, code: str, *, retry: bool) -> None:
        with self.session_factory() as session:
            # Failure recording does not need the initiating actor to remain authorized.
            from backend.app.domains.workspace.tenants.models import Workspace

            session.scalar(
                select(Workspace).where(Workspace.id == lease.workspace_id).with_for_update()
            )
            job = session.scalar(
                select(PluginDownload)
                .where(
                    PluginDownload.workspace_id == lease.workspace_id,
                    PluginDownload.id == lease.job_id,
                )
                .with_for_update()
            )
            if job is None or job.lease_token != lease.token or job.status != "fetching":
                return
            job.status = "queued" if retry and job.attempts < 3 else "failed"
            job.error_code, job.lease_token = code, None
            job.lease_until = (
                datetime.now(UTC) + timedelta(seconds=30 * job.attempts)
                if job.status == "queued"
                else None
            )
            AuditService(session).record_system_action(
                workspace_id=job.workspace_id,
                action="plugin.download_" + job.status,
                target_type="plugin_distribution",
                target_id=job.id,
                metadata={"error_code": code, "attempts": job.attempts},
            )
            session.commit()
