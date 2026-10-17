"""Workspace credential service: save, rotate, revoke, resolve.

Every management operation requires the space manage role and records its
audit fact without the secret. Resolution is the opposite boundary: it
takes no actor, because its only caller is the worker-side probe that has
already re-admitted its target, and it refuses a revoked credential so a
rotation or revocation that lands while a probe is queued stops it.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.identity.context import CurrentUser
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.secrets import SecretCipher
from opsmesh.workspaces.credentials.models import (
    CREDENTIAL_KIND_MODEL_PROVIDER,
    CREDENTIAL_STATUS_ACTIVE,
    CREDENTIAL_STATUS_REVOKED,
    WorkspaceCredential,
)
from opsmesh.workspaces.credentials.schemas import validated_kind
from opsmesh.workspaces.members import access


def create_credential(
    session: Session,
    *,
    actor: CurrentUser,
    workspace_id: UUID,
    name: str,
    kind: str,
    secret: Mapping[str, str],
    cipher: SecretCipher,
) -> WorkspaceCredential:
    """Encrypt and store one workspace secret under a unique active name."""
    access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id, permission="manage", lock=True
    )
    stated_kind = validated_kind(kind)
    encrypted = cipher.encrypt_payload(dict(secret))
    credential = WorkspaceCredential(
        workspace_id=workspace_id,
        name=name,
        kind=stated_kind,
        ciphertext=encrypted.ciphertext,
        fingerprint=encrypted.fingerprint,
        key_id=encrypted.key_id,
        revision=1,
        status=CREDENTIAL_STATUS_ACTIVE,
        created_by_user_id=actor.user_id,
    )
    session.add(credential)
    try:
        session.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "An active credential with this name already exists",
            details={"name": name},
        ) from exc
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id),
        action="workspace.credential_created",
        target_type="workspace_credential",
        target_id=str(credential.id),
        summary="Workspace credential created",
        details={"name": name, "kind": stated_kind, "fingerprint": encrypted.fingerprint},
    )
    return credential


def list_credentials(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, page: PageParams
) -> tuple[list[WorkspaceCredential], int]:
    """List credential metadata; the ciphertext is never selected for a view."""
    access.require_workspace_member(session, workspace_id=workspace_id, user_id=actor.user_id)
    statement = (
        select(WorkspaceCredential)
        .where(WorkspaceCredential.workspace_id == workspace_id)
        .order_by(WorkspaceCredential.created_at.desc(), WorkspaceCredential.id)
    )
    return page_scalars(session, statement, page)


def get_credential(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, credential_id: UUID
) -> WorkspaceCredential:
    """Read one credential's metadata; a member of the workspace may."""
    access.require_workspace_member(session, workspace_id=workspace_id, user_id=actor.user_id)
    return _credential(session, workspace_id=workspace_id, credential_id=credential_id)


def rotate_credential(
    session: Session,
    *,
    actor: CurrentUser,
    workspace_id: UUID,
    credential_id: UUID,
    secret: Mapping[str, str],
    cipher: SecretCipher,
) -> WorkspaceCredential:
    """Replace the stored secret and advance the revision."""
    access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id, permission="manage", lock=True
    )
    credential = _credential(session, workspace_id=workspace_id, credential_id=credential_id)
    if credential.status != CREDENTIAL_STATUS_ACTIVE:
        raise ConflictError(
            "A revoked credential cannot be rotated",
            details={"status": credential.status},
        )
    encrypted = cipher.encrypt_payload(dict(secret))
    credential.ciphertext = encrypted.ciphertext
    credential.fingerprint = encrypted.fingerprint
    credential.key_id = encrypted.key_id
    credential.revision += 1
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id),
        action="workspace.credential_rotated",
        target_type="workspace_credential",
        target_id=str(credential.id),
        summary="Workspace credential rotated",
        details={"revision": credential.revision, "fingerprint": encrypted.fingerprint},
    )
    return credential


def revoke_credential(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, credential_id: UUID
) -> WorkspaceCredential:
    """Revoke one credential; every future resolution is refused."""
    access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id, permission="manage", lock=True
    )
    credential = _credential(session, workspace_id=workspace_id, credential_id=credential_id)
    if credential.status == CREDENTIAL_STATUS_REVOKED:
        return credential
    credential.status = CREDENTIAL_STATUS_REVOKED
    credential.revoked_at = datetime.now(UTC)
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id),
        action="workspace.credential_revoked",
        target_type="workspace_credential",
        target_id=str(credential.id),
        summary="Workspace credential revoked",
        details={"revision": credential.revision},
    )
    return credential


def resolve_credential_secret(
    session: Session,
    *,
    workspace_id: UUID,
    credential_id: UUID,
    cipher: SecretCipher,
) -> dict[str, object]:
    """Decrypt one active credential at the boundary that needs its plaintext.

    This is the only decryption path in the module: no route, event, log
    or response may call it. A revoked credential refuses resolution, so a
    revocation that lands after a probe was queued still stops it.
    """
    credential = _credential(session, workspace_id=workspace_id, credential_id=credential_id)
    if credential.status != CREDENTIAL_STATUS_ACTIVE:
        raise ForbiddenError(
            "The credential is revoked",
            details={"credential_id": str(credential_id), "status": credential.status},
        )
    return cipher.decrypt_payload(credential.ciphertext, key_id=credential.key_id)


def _credential(
    session: Session, *, workspace_id: UUID, credential_id: UUID
) -> WorkspaceCredential:
    credential = session.scalar(
        select(WorkspaceCredential).where(
            WorkspaceCredential.id == credential_id,
            WorkspaceCredential.workspace_id == workspace_id,
        )
    )
    if credential is None:
        raise NotFoundError("The workspace credential does not exist")
    return credential


def _actor(actor: CurrentUser, *, workspace_id: UUID) -> AuditActor:
    return AuditActor(actor_type="user", actor_id=str(actor.user_id), workspace_id=workspace_id)


__all__ = [
    "CREDENTIAL_KIND_MODEL_PROVIDER",
    "create_credential",
    "get_credential",
    "list_credentials",
    "resolve_credential_secret",
    "revoke_credential",
    "rotate_credential",
]
