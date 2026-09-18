"""Workspace-approved sources and explicit installation of verified candidates."""

import base64
import re
from importlib.metadata import version
from uuid import UUID

from opsmesh_plugin_sdk.distribution import PluginCatalog, SignedPluginRelease, verify_release
from packaging.specifiers import SpecifierSet
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.db.errors import flush_or_raise_conflict
from backend.app.core.errors import ConflictError, NotFoundError, PolicyDeniedError
from backend.app.core.utils import payload_hash
from backend.app.domains.capabilities.plugins.contracts import (
    CandidateApproval,
    CandidatePreviewRequest,
    PluginInstallRequest,
    SourceSettings,
    SourceUpdate,
)
from backend.app.domains.capabilities.plugins.models import (
    PluginBinding,
    PluginCandidate,
    PluginDownload,
    PluginInstall,
    PluginRelease,
    PluginSource,
    PluginTrustKey,
)
from backend.app.domains.capabilities.plugins.policy import resource_configuration
from backend.app.domains.capabilities.plugins.service import PluginService
from backend.app.domains.capabilities.plugins.transport import validate_distribution_url
from backend.app.domains.capabilities.resources.schema import reject_embedded_secrets
from backend.app.observability.audit.service import AuditService


class PluginDistributionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def authorize(self, workspace_id: UUID, user_id: UUID) -> None:
        PluginService(self.session).require_admin(workspace_id, user_id)

    def source(self, workspace_id: UUID, source_id: UUID) -> PluginSource:
        row = self.session.scalar(
            select(PluginSource)
            .where(
                PluginSource.workspace_id == workspace_id,
                PluginSource.id == source_id,
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("Plugin source not found")
        return row

    def candidate(self, workspace_id: UUID, candidate_id: UUID) -> PluginCandidate:
        row = self.session.scalar(
            select(PluginCandidate)
            .where(
                PluginCandidate.workspace_id == workspace_id,
                PluginCandidate.id == candidate_id,
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("Plugin candidate not found")
        return row

    def configure(
        self,
        workspace_id: UUID,
        user_id: UUID,
        request: SourceSettings,
        source_id: UUID | None = None,
    ) -> PluginSource:
        self.authorize(workspace_id, user_id)
        hosts = sorted(set(request.allowed_hosts))
        if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", h) for h in hosts):
            raise ValueError("Use exact lowercase distribution hostnames")
        validate_distribution_url(request.url, hosts)
        if source_id is None:
            source = PluginSource(workspace_id=workspace_id)
            self.session.add(source)
        else:
            source = self.source(workspace_id, source_id)
            if (
                not isinstance(request, SourceUpdate)
                or request.expected_generation != source.generation
            ):
                raise ConflictError("Plugin source changed; reload before modifying")
            source.generation += 1
        for field in ("name", "url", "sha256", "enabled"):
            setattr(source, field, getattr(request, field))
        source.allowed_hosts = hosts
        flush_or_raise_conflict(self.session, "Plugin source name already exists")
        self.audit(
            workspace_id,
            user_id,
            "source_configured",
            source.id,
            {"generation": source.generation, "enabled": source.enabled},
        )
        self.session.commit()
        return source

    def enqueue(
        self,
        workspace_id: UUID,
        user_id: UUID,
        source_id: UUID,
        request_key: str,
        candidate_id: UUID | None = None,
    ) -> PluginDownload:
        self.authorize(workspace_id, user_id)
        source = self.source(workspace_id, source_id)
        if not source.enabled:
            raise PolicyDeniedError("Plugin source is disabled")
        if candidate_id is not None:
            if source.synced_generation != source.generation:
                raise ConflictError("Sync the approved source before downloading candidates")
            candidate = self.candidate(workspace_id, candidate_id)
            if candidate.source_id != source.id or candidate.withdrawn:
                raise PolicyDeniedError("Plugin candidate is unavailable")
        existing = self.session.scalar(
            select(PluginDownload).where(
                PluginDownload.workspace_id == workspace_id,
                PluginDownload.request_key == request_key,
            )
        )
        if existing is not None:
            if (existing.source_id, existing.candidate_id, existing.source_generation) != (
                source_id,
                candidate_id,
                source.generation,
            ):
                raise ConflictError("Download request key already has another intent")
            self.session.commit()
            return existing
        job = PluginDownload(
            workspace_id=workspace_id,
            source_id=source_id,
            candidate_id=candidate_id,
            actor_id=user_id,
            request_key=request_key,
            source_generation=source.generation,
        )
        self.session.add(job)
        self.session.flush()
        self.audit(workspace_id, user_id, "download_queued", job.id, {})
        self.session.commit()
        return job

    def job(self, workspace_id: UUID, job_id: UUID) -> PluginDownload:
        job = self.session.scalar(
            select(PluginDownload)
            .where(
                PluginDownload.workspace_id == workspace_id,
                PluginDownload.id == job_id,
            )
            .execution_options(populate_existing=True)
        )
        if job is None:
            raise NotFoundError("Plugin download not found")
        return job

    def retry(self, workspace_id: UUID, user_id: UUID, job_id: UUID) -> PluginDownload:
        self.authorize(workspace_id, user_id)
        job = self.job(workspace_id, job_id)
        source = self.source(workspace_id, job.source_id)
        if (
            job.status != "failed"
            or not source.enabled
            or source.generation != job.source_generation
        ):
            raise ConflictError("Only failed downloads for the current source can be retried")
        job.status, job.attempts, job.error_code = "queued", 0, None
        job.actor_id = user_id
        self.audit(workspace_id, user_id, "download_retried", job.id, {})
        self.session.commit()
        return job

    def apply_catalog(self, source: PluginSource, catalog: PluginCatalog) -> None:
        existing = {
            (row.plugin_key, row.version): row
            for row in self.session.scalars(
                select(PluginCandidate).where(
                    PluginCandidate.workspace_id == source.workspace_id,
                    PluginCandidate.source_id == source.id,
                )
            )
        }
        seen: set[tuple[str, str]] = set()
        for entry in catalog.entries:
            validate_distribution_url(entry.release_url, source.allowed_hosts)
            identity = (entry.plugin_key, entry.version)
            row = existing.get(identity)
            if row is None:
                row = PluginCandidate(
                    workspace_id=source.workspace_id,
                    source_id=source.id,
                    plugin_key=entry.plugin_key,
                    version=entry.version,
                    publisher_key_id=entry.publisher_key_id,
                    url=entry.release_url,
                    sha256=entry.sha256,
                )
                self.session.add(row)
            elif (row.url, row.sha256, row.publisher_key_id) != (
                entry.release_url,
                entry.sha256,
                entry.publisher_key_id,
            ):
                raise ConflictError("Catalog release identity is immutable")
            row.withdrawn = entry.withdrawn
            seen.add(identity)
        for identity, row in existing.items():
            if identity not in seen:
                row.withdrawn = True
        source.synced_generation = source.generation

    def verify(self, candidate: PluginCandidate, release: SignedPluginRelease) -> None:
        package = release.release.package
        if (package.manifest.key, package.manifest.version, package.publisher_key_id) != (
            candidate.plugin_key,
            candidate.version,
            candidate.publisher_key_id,
        ):
            raise ValueError("Release identity does not match catalog")
        key = self.session.scalar(
            select(PluginTrustKey)
            .where(
                PluginTrustKey.workspace_id == candidate.workspace_id,
                PluginTrustKey.key_id == candidate.publisher_key_id,
                PluginTrustKey.plugin_key == candidate.plugin_key,
                PluginTrustKey.status == "active",
            )
            .with_for_update()
        )
        if key is None:
            raise PolicyDeniedError("Publisher key is not trusted")
        verify_release(release, base64.b64decode(key.public_key, validate=True))
        reject_embedded_secrets(release.model_dump(mode="json"))
        validate_distribution_url(
            release.release.source_repository, [release.release.source_repository.split("/")[2]]
        )
        for package_name, requirement in (
            ("opsmesh", release.release.platform_requires),
            ("opsmesh-plugin-sdk", release.release.sdk_requires),
        ):
            if not SpecifierSet(requirement).contains(version(package_name), prereleases=True):
                raise PolicyDeniedError("Plugin is incompatible with the installed platform or SDK")

    def preview(
        self,
        workspace_id: UUID,
        user_id: UUID,
        candidate_id: UUID,
        request: CandidatePreviewRequest,
    ) -> dict[str, object]:
        self.authorize(workspace_id, user_id)
        candidate = self.candidate(workspace_id, candidate_id)
        source = self.source(workspace_id, candidate.source_id)
        if (
            not source.enabled
            or source.synced_generation != source.generation
            or candidate.withdrawn
            or candidate.verified_release is None
        ):
            raise PolicyDeniedError("Plugin candidate is not available and verified")
        release = SignedPluginRelease.model_validate(candidate.verified_release)
        self.verify(candidate, release)
        install = self.session.scalar(
            select(PluginInstall).where(
                PluginInstall.workspace_id == workspace_id,
                PluginInstall.plugin_key == candidate.plugin_key,
            )
        )
        previous = (
            None
            if install is None
            else self.session.scalar(
                select(PluginRelease).where(
                    PluginRelease.workspace_id == workspace_id,
                    PluginRelease.install_id == install.id,
                    PluginRelease.version == install.current_version,
                )
            )
        )
        old_permissions = set(previous.approved_permissions if previous else [])
        manifest = release.release.package.manifest
        permissions = {p for cap in manifest.capabilities for p in cap.required_permissions}
        schemas = {cap.key: cap.configuration_schema for cap in manifest.capabilities}
        if set(request.bindings) - set(schemas):
            raise ValueError("Unknown capability binding")
        proposed = {
            cap.key: {
                "resource_id": str(request.bindings[cap.key].resource_id),
                "configuration": resource_configuration(
                    self.session, workspace_id, cap.kind, request.bindings[cap.key].resource_id
                ),
            }
            for cap in manifest.capabilities
            if cap.key in request.bindings
        }
        reject_embedded_secrets(proposed)
        current = (
            {}
            if previous is None
            else {
                row.capability_key: {
                    "resource_id": str(row.resource_id),
                    "configuration": row.configuration,
                }
                for row in self.session.scalars(
                    select(PluginBinding).where(
                        PluginBinding.workspace_id == workspace_id,
                        PluginBinding.release_id == previous.id,
                    )
                )
            }
        )
        result: dict[str, object] = {
            "candidate_id": str(candidate.id),
            "source_generation": source.generation,
            "sha256": candidate.sha256,
            "release": release.release.model_dump(mode="json"),
            "expected_generation": install.generation if install else None,
            "current_version": install.current_version if install else None,
            "required_permissions": sorted(permissions),
            "added_permissions": sorted(permissions - old_permissions),
            "removed_permissions": sorted(old_permissions - permissions),
            "configuration_schemas": schemas,
            "current_bindings": current,
            "proposed_bindings": proposed,
        }
        result["preview_digest"] = payload_hash(result)
        return result

    def install(
        self,
        workspace_id: UUID,
        user_id: UUID,
        candidate_id: UUID,
        request: CandidateApproval,
    ) -> PluginInstall:
        preview = self.preview(
            workspace_id, user_id, candidate_id, CandidatePreviewRequest(bindings=request.bindings)
        )
        if request.preview_digest != preview["preview_digest"]:
            raise ConflictError("Preview changed; review permissions and bindings again")
        candidate = self.candidate(workspace_id, candidate_id)
        release = SignedPluginRelease.model_validate(candidate.verified_release)
        install = PluginService(self.session).install(
            workspace_id,
            user_id,
            PluginInstallRequest(
                package=release.release.package,
                bindings=request.bindings,
                approved_permissions=request.approved_permissions,
                expected_generation=request.expected_generation,
            ),
            commit=False,
        )
        self.audit(
            workspace_id,
            user_id,
            "candidate_installed",
            candidate.id,
            {"install_id": str(install.id), "sha256": candidate.sha256},
        )
        self.session.commit()
        return install

    def audit(
        self,
        workspace_id: UUID,
        user_id: UUID,
        action: str,
        target_id: UUID,
        metadata: dict[str, object],
    ) -> None:
        AuditService(self.session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="plugin." + action,
            target_type="plugin_distribution",
            target_id=target_id,
            metadata=metadata,
        )
