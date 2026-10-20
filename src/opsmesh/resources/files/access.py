"""File authorization contracts and the injected scope and quota boundaries.

Reads and writes are authorized here: the owner always may, a grantee may
exactly what its grant permits, and nobody else may. Two boundaries are
injected rather than imported — the external scope admission, whose real
owner arrives with the workspace step, and the storage quota ledger, which
belongs to identity. Both default to an explicit refusal so a missing
assembly fails closed instead of silently granting access.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.resources.files.models import (
    FILE_PERMISSION_DELETE,
    FILE_PERMISSION_READ,
    FILE_PERMISSION_USE_IN_EXECUTION,
    FILE_PERMISSION_WRITE,
    FILE_PERMISSIONS,
    ResourceFile,
    ResourceFileGrant,
)
from opsmesh.shared.errors import ForbiddenError, InvalidArgumentError


@dataclass(frozen=True)
class ResourceReader:
    """The verified reader of one request; built by the assembled routes."""

    user_id: UUID


@dataclass(frozen=True)
class ScopeRef:
    """One external scope a resource request may name.

    A workspace or project scope is admitted by the injected owner: the
    assembled application delegates to the real space and project
    membership checks, and the default refuses until that assembly.
    """

    configuration_id: UUID | None = None

    @property
    def is_user_scope(self) -> bool:
        return self.configuration_id is None


@runtime_checkable
class ScopeAdmission(Protocol):
    """Whether one reader may act inside one external scope.

    The admission runs inside the caller's session, exactly like the
    storage ledger: the real owner checks membership in the same
    transaction that writes the resource fact.
    """

    def admit(self, session: Session, *, reader: ResourceReader, scope: ScopeRef) -> None:
        """Raise a domain error when the scope is not admitted for the reader."""
        ...


class RejectingScopeAdmission:
    """The default admission: the user scope passes, every external scope is refused.

    The workspace owner replaces this in the workspace step; until then a
    request that names a workspace or project scope fails closed instead of
    being treated as user scope. The user scope itself needs no external
    owner: the file-level owner and grant checks authorize it.
    """

    def admit(self, session: Session, *, reader: ResourceReader, scope: ScopeRef) -> None:
        if scope.is_user_scope:
            return
        _ = session, reader
        raise ForbiddenError(
            "The resource scope is not admitted",
            details={
                "configuration_id": str(scope.configuration_id) if scope.configuration_id else None,
            },
        )


@runtime_checkable
class StorageQuotaLedger(Protocol):
    """The identity-owned storage ledger, called inside the caller's session."""

    def reserve_storage(
        self,
        session: Session,
        *,
        user_id: UUID,
        amount: int,
        idempotency_scope: str,
        idempotency_key: str,
    ) -> UUID:
        """Hold one storage upper bound in bytes and return its reservation id."""
        ...

    def settle_storage(self, session: Session, *, reservation_id: UUID, actual_amount: int) -> None:
        """Settle one reservation with its actual stored bytes."""
        ...

    def release_storage(self, session: Session, *, reservation_id: UUID) -> None:
        """Release one reservation without recording usage."""
        ...


def validate_permissions(permissions: Sequence[str]) -> list[str]:
    """Refuse an unknown or duplicated file permission."""
    validated: list[str] = []
    for permission in permissions:
        if permission not in FILE_PERMISSIONS:
            raise InvalidArgumentError(
                "The file permission is unknown",
                details={"known_permissions": list(FILE_PERMISSIONS)},
            )
        if permission in validated:
            raise InvalidArgumentError(
                "The file permission is declared twice", details={"permission": permission}
            )
        validated.append(permission)
    return validated


def authorize_read(
    file: ResourceFile, *, reader: ResourceReader, grant: ResourceFileGrant | None
) -> None:
    """The owner or a read grantee may read one file."""
    if file.owner_user_id == reader.user_id:
        return
    if grant is not None and FILE_PERMISSION_READ in grant.permissions:
        return
    raise ForbiddenError("The reader may not access this file")


def authorize_execution(
    file: ResourceFile, *, reader: ResourceReader, grant: ResourceFileGrant | None
) -> None:
    """Execution permission is independent of ordinary content read access."""
    if file.owner_user_id == reader.user_id:
        return
    if grant is not None and FILE_PERMISSION_USE_IN_EXECUTION in grant.permissions:
        return
    raise ForbiddenError("The reader may not use this file in execution")


def authorize_write(
    file: ResourceFile, *, reader: ResourceReader, grant: ResourceFileGrant | None
) -> None:
    """The owner or a write grantee may change one file's content."""
    if file.owner_user_id == reader.user_id:
        return
    if grant is not None and FILE_PERMISSION_WRITE in grant.permissions:
        return
    raise ForbiddenError("The reader may not modify this file")


def authorize_delete(
    file: ResourceFile, *, reader: ResourceReader, grant: ResourceFileGrant | None
) -> None:
    """The owner or a delete grantee may delete one file."""
    if file.owner_user_id == reader.user_id:
        return
    if grant is not None and FILE_PERMISSION_DELETE in grant.permissions:
        return
    raise ForbiddenError("The reader may not delete this file")


def authorize_manage(file: ResourceFile, *, reader: ResourceReader) -> None:
    """Only the owner manages grants and the runtime policy."""
    if file.owner_user_id != reader.user_id:
        raise ForbiddenError("Only the file owner manages its grants and policy")


__all__ = [
    "RejectingScopeAdmission",
    "ResourceReader",
    "ScopeAdmission",
    "ScopeRef",
    "StorageQuotaLedger",
    "authorize_delete",
    "authorize_execution",
    "authorize_manage",
    "authorize_read",
    "authorize_write",
    "validate_permissions",
]
