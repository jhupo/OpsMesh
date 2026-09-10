from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.capabilities.mcp_credentials import (
    McpCredentialReferenceCreateRequest,
    McpCredentialReferenceRotateRequest,
    McpCredentialReferenceUpdateRequest,
)
from backend.app.audit.service import AuditService
from backend.app.capabilities.mcp_server_helpers import require_mcp_server
from backend.app.capabilities.models import McpCredentialReference
from backend.app.core.config import Settings, get_settings
from backend.app.core.pagination import PageParams
from backend.app.db.errors import commit_or_raise_conflict, flush_or_raise_conflict
from backend.app.db.pagination import page_scalars
from backend.app.reviews.approval_service import ResourceReviewApprovalService
from backend.app.reviews.constants import (
    RESOURCE_STATUS_ACTIVE,
    RESOURCE_STATUS_PENDING_APPROVAL,
    REVIEW_TYPE_MCP_CREDENTIAL_REFERENCE,
)
from backend.app.reviews.models import ResourceReview
from backend.app.reviews.service import ResourcePolicyReviewBuilder
from backend.app.secrets.service import SecretEncryptionService


class McpCredentialService:
    def __init__(
        self,
        session: Session,
        secret_service: SecretEncryptionService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._secret_service = secret_service
        self._settings = settings or get_settings()

    def create_credential_reference(
        self,
        workspace_id: UUID,
        data: McpCredentialReferenceCreateRequest,
        actor_user_id: UUID | None = None,
    ) -> McpCredentialReference:
        server = None
        if data.mcp_server_id is not None:
            server = require_mcp_server(self._session, workspace_id, data.mcp_server_id)
        review = ResourcePolicyReviewBuilder(
            self._session,
            self._settings,
        ).review_mcp_credential_reference(
            workspace_id=workspace_id,
            visibility=server.visibility if server is not None else "private",
            mcp_server_id=data.mcp_server_id,
            name=data.name,
            provider=data.provider,
            external_ref=data.external_ref,
            scopes=data.scopes,
            has_secret_payload=data.secret_payload is not None,
        )
        credential = McpCredentialReference(
            workspace_id=workspace_id,
            status=RESOURCE_STATUS_PENDING_APPROVAL if review.required else RESOURCE_STATUS_ACTIVE,
            **data.model_dump(exclude={"secret_payload"}),
        )
        if data.secret_payload is not None:
            if self._secret_service is None:
                raise ValueError("Hosted credential encryption is not configured")
            encrypted = self._secret_service.encrypt_payload(data.secret_payload)
            credential.provider = "hosted"
            credential.external_ref = ""
            credential.encrypted_secret_payload = encrypted.ciphertext
            credential.secret_fingerprint = encrypted.fingerprint
            credential.encryption_key_id = encrypted.key_id
        self._session.add(credential)
        flush_or_raise_conflict(self._session, "MCP credential name already exists")
        if review.required:
            ResourceReviewApprovalService(self._session).request_resource_review(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                approval_type=REVIEW_TYPE_MCP_CREDENTIAL_REFERENCE,
                target_type="mcp_credential_reference",
                target_id=credential.id,
                target_name=credential.name,
                review=review,
                snapshot={
                    "id": str(credential.id),
                    "mcp_server_id": str(credential.mcp_server_id)
                    if credential.mcp_server_id is not None
                    else None,
                    "name": credential.name,
                    "provider": credential.provider,
                    "external_ref_configured": bool(credential.external_ref),
                    "has_hosted_secret": credential.encrypted_secret_payload is not None,
                    "secret_fingerprint": credential.secret_fingerprint,
                    "scopes": list(credential.scopes),
                    "status": credential.status,
                },
            )
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action=(
                    "mcp_credential.review_requested"
                    if review.required
                    else "mcp_credential.created"
                ),
                target_type="mcp_credential_reference",
                target_id=credential.id,
                metadata={
                    "name": credential.name,
                    "mcp_server_id": str(credential.mcp_server_id)
                    if credential.mcp_server_id is not None
                    else None,
                    "provider": credential.provider,
                    "has_hosted_secret": credential.encrypted_secret_payload is not None,
                    "review_required": review.required,
                    "review_risk_level": review.risk_level,
                    "review_reasons": review.reasons,
                },
            )
        commit_or_raise_conflict(self._session, "MCP credential name already exists")
        self._session.refresh(credential)
        return credential

    def update_credential_reference(
        self,
        workspace_id: UUID,
        credential_id: UUID,
        data: McpCredentialReferenceUpdateRequest,
        actor_user_id: UUID | None = None,
    ) -> McpCredentialReference:
        credential = self._require_credential(workspace_id, credential_id)
        if data.name is None and data.scopes is None:
            raise ValueError("Provide name or scopes to update MCP credential")
        server_visibility = self._credential_server_visibility(workspace_id, credential)
        next_name = data.name if data.name is not None else credential.name
        next_scopes = list(data.scopes) if data.scopes is not None else list(credential.scopes)
        review = ResourcePolicyReviewBuilder(
            self._session,
            self._settings,
        ).review_mcp_credential_reference(
            workspace_id=workspace_id,
            visibility=server_visibility,
            mcp_server_id=credential.mcp_server_id,
            name=next_name,
            provider=credential.provider,
            external_ref=credential.external_ref,
            scopes=next_scopes,
            has_secret_payload=credential.encrypted_secret_payload is not None,
        )
        if data.name is not None:
            credential.name = data.name
        if data.scopes is not None:
            credential.scopes = next_scopes
        if review.required:
            credential.status = RESOURCE_STATUS_PENDING_APPROVAL
            self._request_review(credential, workspace_id, actor_user_id, review)
        if actor_user_id is not None:
            self._record_change_audit(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                credential=credential,
                action=(
                    "mcp_credential.review_requested"
                    if review.required
                    else "mcp_credential.updated"
                ),
                review=review,
            )
        flush_or_raise_conflict(self._session, "MCP credential name already exists")
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def rotate_credential_reference(
        self,
        workspace_id: UUID,
        credential_id: UUID,
        data: McpCredentialReferenceRotateRequest,
        actor_user_id: UUID | None = None,
    ) -> McpCredentialReference:
        credential = self._require_credential(workspace_id, credential_id)
        if data.secret_payload is not None:
            if self._secret_service is None:
                raise ValueError("Hosted credential encryption is not configured")
            encrypted = self._secret_service.encrypt_payload(data.secret_payload)
            next_provider = "hosted"
            next_external_ref = ""
        else:
            next_provider = data.provider or ""
            next_external_ref = (data.external_ref or "").strip()
            if not next_provider or not next_external_ref:
                raise ValueError("External credential rotation requires provider and external_ref")
            encrypted = None
        server_visibility = self._credential_server_visibility(workspace_id, credential)
        review = ResourcePolicyReviewBuilder(
            self._session,
            self._settings,
        ).review_mcp_credential_reference(
            workspace_id=workspace_id,
            visibility=server_visibility,
            mcp_server_id=credential.mcp_server_id,
            name=credential.name,
            provider=next_provider,
            external_ref=next_external_ref,
            scopes=list(credential.scopes),
            has_secret_payload=data.secret_payload is not None,
        )
        credential.provider = next_provider
        credential.external_ref = next_external_ref
        credential.encrypted_secret_payload = encrypted.ciphertext if encrypted else None
        credential.secret_fingerprint = encrypted.fingerprint if encrypted else None
        credential.encryption_key_id = encrypted.key_id if encrypted else None
        if review.required:
            credential.status = RESOURCE_STATUS_PENDING_APPROVAL
            self._request_review(credential, workspace_id, actor_user_id, review)
        if actor_user_id is not None:
            self._record_change_audit(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                credential=credential,
                action=(
                    "mcp_credential.review_requested"
                    if review.required
                    else "mcp_credential.rotated"
                ),
                review=review,
            )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def list_credential_references(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        mcp_server_id: UUID | None = None,
        include_disabled: bool = False,
    ) -> tuple[list[McpCredentialReference], int]:
        if mcp_server_id is not None:
            require_mcp_server(self._session, workspace_id, mcp_server_id)
        statement = select(McpCredentialReference).where(
            McpCredentialReference.workspace_id == workspace_id
        )
        if mcp_server_id is not None:
            statement = statement.where(McpCredentialReference.mcp_server_id == mcp_server_id)
        if not include_disabled:
            statement = statement.where(McpCredentialReference.status == "active")
        return self._page(
            statement.order_by(
                McpCredentialReference.status.asc(),
                McpCredentialReference.created_at.desc(),
            ),
            page,
        )

    def disable_credential_reference(
        self,
        workspace_id: UUID,
        credential_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> McpCredentialReference:
        credential = self._session.get(McpCredentialReference, credential_id)
        if credential is None or credential.workspace_id != workspace_id:
            raise ValueError("MCP credential reference not found")
        credential.status = "disabled"
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_credential.disabled",
                target_type="mcp_credential_reference",
                target_id=credential.id,
                metadata={
                    "name": credential.name,
                    "mcp_server_id": str(credential.mcp_server_id)
                    if credential.mcp_server_id is not None
                    else None,
                },
            )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def _require_credential(
        self,
        workspace_id: UUID,
        credential_id: UUID,
    ) -> McpCredentialReference:
        credential = self._session.get(McpCredentialReference, credential_id)
        if credential is None or credential.workspace_id != workspace_id:
            raise ValueError("MCP credential reference not found")
        return credential

    def _credential_server_visibility(
        self,
        workspace_id: UUID,
        credential: McpCredentialReference,
    ) -> str:
        if credential.mcp_server_id is None:
            return "private"
        return require_mcp_server(self._session, workspace_id, credential.mcp_server_id).visibility

    def _request_review(
        self,
        credential: McpCredentialReference,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        review: ResourceReview,
    ) -> None:
        ResourceReviewApprovalService(self._session).request_resource_review(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            approval_type=REVIEW_TYPE_MCP_CREDENTIAL_REFERENCE,
            target_type="mcp_credential_reference",
            target_id=credential.id,
            target_name=credential.name,
            review=review,
            snapshot={
                "id": str(credential.id),
                "mcp_server_id": str(credential.mcp_server_id)
                if credential.mcp_server_id is not None
                else None,
                "name": credential.name,
                "provider": credential.provider,
                "external_ref_configured": bool(credential.external_ref),
                "has_hosted_secret": credential.encrypted_secret_payload is not None,
                "secret_fingerprint": credential.secret_fingerprint,
                "scopes": list(credential.scopes),
                "status": credential.status,
            },
        )

    def _record_change_audit(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        credential: McpCredentialReference,
        action: str,
        review: ResourceReview,
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action=action,
            target_type="mcp_credential_reference",
            target_id=credential.id,
            metadata={
                "name": credential.name,
                "mcp_server_id": str(credential.mcp_server_id)
                if credential.mcp_server_id is not None
                else None,
                "provider": credential.provider,
                "external_ref_configured": bool(credential.external_ref),
                "has_hosted_secret": credential.encrypted_secret_payload is not None,
                "secret_fingerprint": credential.secret_fingerprint,
                "review_required": review.required,
                "review_risk_level": review.risk_level,
                "review_reasons": review.reasons,
            },
        )

    def _page(
        self,
        statement: Select[tuple[McpCredentialReference]],
        page: PageParams,
    ) -> tuple[list[McpCredentialReference], int]:
        return page_scalars(self._session, statement, page)
