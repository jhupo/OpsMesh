"""Private, event-bound channel uploads backed by workspace object storage."""

from hashlib import sha256
from uuid import UUID

from opsmesh_plugin_sdk.messaging.contracts import (
    AttachmentUpload,
    IncomingMessage,
    MessageAttachment,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError
from backend.app.domains.access.context import AuthenticatedUser
from backend.app.domains.access.execution import ExecutionIdentityService
from backend.app.domains.access.resource_queries import execution_resource_queries
from backend.app.domains.access.resources import (
    ResourceAccessDenied,
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from backend.app.domains.capabilities.plugins.services import PluginPrincipal, PluginServices
from backend.app.domains.capabilities.resources.schema import reject_embedded_secrets
from backend.app.domains.integrations.automation_authorization import require_automation_principal
from backend.app.domains.integrations.automation_contracts import AutomationConfiguration
from backend.app.domains.integrations.automation_models import Automation
from backend.app.domains.integrations.identities import ExternalIdentityService
from backend.app.domains.workspace.storage.models import WorkspaceFile
from backend.app.domains.workspace.storage.security import safe_filename
from backend.app.domains.workspace.storage.service import WorkspaceFileService


class PluginAttachmentService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upload(
        self,
        principal: PluginPrincipal,
        automation_id: UUID,
        request: AttachmentUpload,
        content: bytes,
        files: WorkspaceFileService,
    ) -> MessageAttachment:
        services = PluginServices(self.session)
        services.require_automation(principal, automation_id, "attachments.write")
        item = self.session.scalar(
            select(Automation).where(
                Automation.workspace_id == principal.workspace_id,
                Automation.id == automation_id,
                Automation.status == "active",
            )
        )
        if item is None:
            raise ResourceAccessDenied()
        config = AutomationConfiguration.model_validate(item.configuration)
        if (
            config.trigger_type != "message"
            or request.sender_id not in config.allowed_senders
            or request.kind not in config.allowed_attachment_kinds
        ):
            raise ResourceAccessDenied()
        require_automation_principal(self.session, item)
        actor = ExecutionIdentityService(self.session).restore(
            principal.workspace_id,
            ExternalIdentityService(self.session).resolve(item, request.sender_id),
        )
        if not 0 < len(content) <= 20 * 1024 * 1024:
            raise DomainError("Attachment exceeds limit", code="attachment_size", status_code=413)
        if (request.kind == "audio") != bool(request.transcript.strip()):
            raise DomainError(
                "Audio requires a transcript; other attachments cannot supply one",
                code="attachment_transcript",
                status_code=422,
            )
        permitted = {
            "image": {"image/png", "image/jpeg", "image/webp", "image/gif"},
            "audio": {"audio/amr", "audio/ogg", "audio/wav", "audio/mpeg", "audio/mp4"},
            "file": {
                "text/plain",
                "text/csv",
                "text/markdown",
                "application/json",
                "application/pdf",
            },
        }
        if request.content_type not in permitted[request.kind]:
            raise DomainError(
                "Attachment type is not enabled", code="attachment_type", status_code=415
            )
        reject_embedded_secrets(request.transcript)
        if request.content_type.startswith("text/") or request.content_type == "application/json":
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise DomainError(
                    "File must be UTF-8", code="attachment_encoding", status_code=422
                ) from exc
            if len(text) > 16000:
                raise DomainError(
                    "Text attachment exceeds 16000 characters",
                    code="attachment_text_size",
                    status_code=413,
                )
            reject_embedded_secrets(text)
        # Serialize the lookup and write so repeated Stream deliveries cannot create new files.
        services.active_install(principal.workspace_id, principal.install_id, lock=True)
        scope = {
            "plugin_install_id": str(principal.install_id),
            "automation_id": str(automation_id),
            "sender_id": request.sender_id,
            "external_event_id": request.external_event_id,
            "slot": request.slot,
            "kind": request.kind,
        }
        query = select(WorkspaceFile).where(WorkspaceFile.workspace_id == principal.workspace_id)
        for key, value in scope.items():
            if key != "kind":
                query = query.where(WorkspaceFile.file_metadata[key].as_string() == str(value))
        existing = self.session.scalar(query)
        if existing is not None:
            ResourceAuthorizationService(self.session, actor).require(
                principal.workspace_id, ResourceKind.FILE, existing.id, ResourceAction.READ
            )
            if (
                existing.status != "active"
                or existing.checksum_sha256 != sha256(content).hexdigest()
                or existing.content_type != request.content_type
                or existing.filename != safe_filename(request.filename)
                or existing.file_metadata.get("kind") != request.kind
                or existing.file_metadata.get("transcript", "") != request.transcript
            ):
                raise DomainError(
                    "Upload slot already used", code="attachment_conflict", status_code=409
                )
            return self.reference(existing)
        count, size = self.session.execute(
            select(
                func.count(WorkspaceFile.id),
                func.coalesce(func.sum(WorkspaceFile.size_bytes), 0),
            ).where(
                WorkspaceFile.workspace_id == principal.workspace_id,
                WorkspaceFile.status == "active",
                WorkspaceFile.file_metadata["plugin_install_id"].as_string()
                == str(principal.install_id),
            )
        ).one()
        if count >= 512 or size + len(content) > 512 * 1024 * 1024:
            raise DomainError(
                "Plugin attachment quota exceeded", code="attachment_quota", status_code=413
            )
        with execution_resource_queries(self.session, principal.workspace_id, actor):
            file = files.upload_file(
                workspace_id=principal.workspace_id,
                uploaded_by_user_id=actor.user_id,
                filename=request.filename,
                content_type=request.content_type,
                content=content,
                metadata={**scope, "slot": str(request.slot), "transcript": request.transcript},
            )
        return self.reference(file)

    def require_references(
        self,
        item: Automation,
        message: IncomingMessage,
        actor: AuthenticatedUser,
        install_id: UUID | None,
    ) -> None:
        config = AutomationConfiguration.model_validate(item.configuration)
        if message.attachments and message.sender_id not in config.allowed_senders:
            raise ResourceAccessDenied()
        for attachment in message.attachments:
            file = self.session.scalar(
                select(WorkspaceFile).where(
                    WorkspaceFile.workspace_id == item.workspace_id,
                    WorkspaceFile.id == attachment.file_id,
                    WorkspaceFile.status == "active",
                )
            )
            if (
                file is None
                or install_id is None
                or attachment.kind not in config.allowed_attachment_kinds
            ):
                raise ResourceAccessDenied()
            expected = {
                "plugin_install_id": str(install_id),
                "automation_id": str(item.id),
                "sender_id": message.sender_id,
                "external_event_id": message.event_id,
            }
            if any(file.file_metadata.get(key) != value for key, value in expected.items()):
                raise ResourceAccessDenied()
            ResourceAuthorizationService(self.session, actor).require(
                item.workspace_id, ResourceKind.FILE, file.id, ResourceAction.READ
            )
            if self.reference(file) != attachment:
                raise ResourceAccessDenied()

    @staticmethod
    def reference(file: WorkspaceFile) -> MessageAttachment:
        return MessageAttachment.model_validate(
            {
                "file_id": file.id,
                "kind": file.file_metadata["kind"],
                "filename": file.filename,
                "content_type": file.content_type,
                "size_bytes": file.size_bytes,
                "checksum_sha256": file.checksum_sha256,
                "transcript": file.file_metadata.get("transcript", ""),
            }
        )
