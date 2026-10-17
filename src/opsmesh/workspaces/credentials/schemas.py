"""Workspace credential HTTP contracts.

No contract carries a secret back out: requests bring one in, views show
the fingerprint, key id and revision that let an operator confirm which
secret is stored without the platform ever returning it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from opsmesh.workspaces.credentials.models import (
    CREDENTIAL_KINDS,
    MAX_NAME_LENGTH,
    MAX_PAYLOAD_ENTRIES,
    MAX_PAYLOAD_KEY_LENGTH,
    MAX_PAYLOAD_VALUE_LENGTH,
)

SecretPayload = Annotated[
    dict[
        Annotated[str, StringConstraints(max_length=MAX_PAYLOAD_KEY_LENGTH)],
        Annotated[str, StringConstraints(max_length=MAX_PAYLOAD_VALUE_LENGTH)],
    ],
    Field(min_length=1, max_length=MAX_PAYLOAD_ENTRIES),
]


class CredentialCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    kind: str = Field(max_length=32)
    secret: SecretPayload


class CredentialRotateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret: SecretPayload


class CredentialView(BaseModel):
    credential_id: UUID
    workspace_id: UUID
    name: str
    kind: str
    fingerprint: str
    key_id: str
    revision: int
    status: str
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None


class CredentialPage(BaseModel):
    credentials: list[CredentialView]
    total: int


def validated_kind(kind: str) -> str:
    """The one place a stated credential kind is checked before storage."""
    if kind not in CREDENTIAL_KINDS:
        raise ValueError(f"Unknown credential kind: {kind}")
    return kind
