"""Workspace credential APIs: create, list, read, rotate and revoke.

The assembled secret cipher is the only source of encryption here; it is
read from the application state exactly like the mail module reads it.
No route returns a secret: the views carry the fingerprint, key id and
revision that identify the stored one.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, status

from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import ForbiddenError
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams
from opsmesh.shared.security.secrets import SecretCipher
from opsmesh.workspaces.credentials import service
from opsmesh.workspaces.credentials.models import WorkspaceCredential
from opsmesh.workspaces.credentials.schemas import (
    CredentialCreateRequest,
    CredentialPage,
    CredentialRotateRequest,
    CredentialView,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/credentials", tags=["workspaces"])

WorkspaceIdPath = Annotated[UUID, Path()]
CredentialIdPath = Annotated[UUID, Path()]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)]
OffsetQuery = Annotated[int, Query(ge=0)]


def get_secret_cipher(request: Request) -> SecretCipher:
    """The assembled encryption boundary; without it no secret is stored."""
    cipher = getattr(request.app.state, "secret_cipher", None)
    if not isinstance(cipher, SecretCipher):
        raise ForbiddenError("The credential cipher is not assembled")
    return cipher


CipherDep = Annotated[SecretCipher, Depends(get_secret_cipher)]


def _view(credential: WorkspaceCredential) -> CredentialView:
    return CredentialView(
        credential_id=credential.id,
        workspace_id=credential.workspace_id,
        name=credential.name,
        kind=credential.kind,
        fingerprint=credential.fingerprint,
        key_id=credential.key_id,
        revision=credential.revision,
        status=credential.status,
        created_by_user_id=credential.created_by_user_id,
        created_at=credential.created_at,
        updated_at=credential.updated_at,
        revoked_at=credential.revoked_at,
    )


@router.post(
    "",
    response_model=CredentialView,
    status_code=status.HTTP_201_CREATED,
    operation_id="workspaces.credentials.create",
)
def create_credential(
    payload: CredentialCreateRequest,
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    cipher: CipherDep,
) -> CredentialView:
    """Store one encrypted workspace secret."""
    with session_transaction(session):
        credential = service.create_credential(
            session,
            actor=user,
            workspace_id=workspace_id,
            name=payload.name,
            kind=payload.kind,
            secret=payload.secret,
            cipher=cipher,
        )
    return _view(credential)


@router.get("", response_model=CredentialPage, operation_id="workspaces.credentials.list")
def list_credentials(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
) -> CredentialPage:
    """List the workspace's credential metadata."""
    credentials, total = service.list_credentials(
        session, actor=user, workspace_id=workspace_id, page=PageParams(limit=limit, offset=offset)
    )
    return CredentialPage(credentials=[_view(item) for item in credentials], total=total)


@router.get(
    "/{credential_id}", response_model=CredentialView, operation_id="workspaces.credentials.read"
)
def read_credential(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    credential_id: CredentialIdPath,
) -> CredentialView:
    return _view(
        service.get_credential(
            session, actor=user, workspace_id=workspace_id, credential_id=credential_id
        )
    )


@router.post(
    "/{credential_id}/rotate",
    response_model=CredentialView,
    operation_id="workspaces.credentials.rotate",
)
def rotate_credential(
    payload: CredentialRotateRequest,
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    credential_id: CredentialIdPath,
    cipher: CipherDep,
) -> CredentialView:
    """Replace the stored secret and advance its revision."""
    with session_transaction(session):
        credential = service.rotate_credential(
            session,
            actor=user,
            workspace_id=workspace_id,
            credential_id=credential_id,
            secret=payload.secret,
            cipher=cipher,
        )
    return _view(credential)


@router.post(
    "/{credential_id}/revoke",
    response_model=CredentialView,
    operation_id="workspaces.credentials.revoke",
)
def revoke_credential(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    credential_id: CredentialIdPath,
) -> CredentialView:
    """Revoke one credential; every future resolution is refused."""
    with session_transaction(session):
        credential = service.revoke_credential(
            session, actor=user, workspace_id=workspace_id, credential_id=credential_id
        )
    return _view(credential)
