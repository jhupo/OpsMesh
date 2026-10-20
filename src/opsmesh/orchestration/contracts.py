"""Composition-owned state and explicit execution operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.execution.contracts import ModelConnection
from opsmesh.shared.errors import ForbiddenError


@dataclass(frozen=True)
class ExecutionSubject:
    """The verified principal of one execution action.

    Platform roles stay in identity and membership stays with configurations;
    this value carries only the principal the ledgers and the
    admission charge. Routes build it from the authenticated user.
    """

    user_id: UUID


@dataclass(frozen=True)
class ExecutionScope:
    """One external scope an execution action may name.

    A configuration scope is admitted by the injected owner; the
    user scope needs no external owner because the session, run and
    environment records are authorized by their own owner checks.
    """

    configuration_id: UUID | None = None

    @property
    def is_user_scope(self) -> bool:
        return self.configuration_id is None


@runtime_checkable
class ScopeAdmission(Protocol):
    """Whether one subject may act inside one external scope.

    The admission runs inside the caller's session, exactly like the
    ledgers: the real owner checks membership in the same transaction
    that writes the execution fact.
    """

    def admit(self, session: Session, *, subject: ExecutionSubject, scope: ExecutionScope) -> None:
        """Raise a domain error when the scope is not admitted for the subject."""
        ...


@runtime_checkable
class SharedResourceAdmission(Protocol):
    """Authorize use of a selected shared runtime resource, without lifecycle control."""

    def admit_resource_use(
        self,
        session: Session,
        *,
        subject: ExecutionSubject,
        scope: ExecutionScope,
        kind: str,
        resource_id: UUID,
        owner_user_id: UUID,
    ) -> None: ...


class RejectingScopeAdmission:
    """The default admission: the user scope passes, every external scope is refused.

    Bootstrap replaces this with configuration admission; until then a
    request that names a configuration scope fails closed instead of
    being treated as user scope.
    """

    def admit(self, session: Session, *, subject: ExecutionSubject, scope: ExecutionScope) -> None:
        if scope.is_user_scope:
            return
        _ = session, subject
        raise ForbiddenError(
            "The execution scope is not admitted",
            details={
                "configuration_id": str(scope.configuration_id) if scope.configuration_id else None,
            },
        )


class ExecutionContext(BaseModel):
    """The neutral context of one execution action.

    It carries the verified principal, the scope value and the current
    operation id. It is built by the caller (route or activity) and never
    reconstructed from persisted or external input.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: UUID
    principal_id: UUID
    owner_ref: str = Field(min_length=1, max_length=200)
    run_id: UUID | None = None
    session_id: UUID | None = None
    operation_id: str | None = Field(default=None, max_length=240)


@runtime_checkable
class ReservationRef(Protocol):
    """The read-only handle of one held ledger reservation.

    A ledger returns its own reservation row through this handle; only its
    id is serializable, so a workflow carries the id and the activity
    resolves the row again inside its own session.
    """

    @property
    def id(self) -> UUID: ...


@runtime_checkable
class TokenLedger(Protocol):
    """The identity-owned user token ledger, called inside the caller's session."""

    def reserve_tokens(
        self,
        session: Session,
        *,
        user_id: UUID,
        amount: int,
        idempotency_scope: str,
        idempotency_key: str,
    ) -> ReservationRef:
        """Hold one token upper bound and return its reservation handle."""
        ...

    def settle_tokens(
        self, session: Session, *, reservation_id: UUID, actual_amount: int
    ) -> object:
        """Settle one reservation with its actual token consumption."""
        ...

    def release_tokens(self, session: Session, *, reservation_id: UUID) -> object:
        """Release one reservation without recording consumption."""
        ...


@runtime_checkable
class CapacityLedger(Protocol):
    """The workspace-owned capacity ledger, called inside the caller's session."""

    def reserve_capacity(
        self,
        session: Session,
        *,
        user_id: UUID,
        kind: str,
        amount: int,
        idempotency_scope: str,
        idempotency_key: str,
    ) -> ReservationRef:
        """Hold one amount of one capacity kind and return its handle."""
        ...

    def release_capacity(self, session: Session, *, user_id: UUID, reservation_id: UUID) -> object:
        """Release one held reservation."""
        ...


class OperationLimit(Protocol):
    """Reserve the source flow's Token upper bound in the same PG transaction."""

    def __call__(
        self,
        session: Session,
        *,
        run_ref: str,
        operation_id: str,
        tokens: int,
    ) -> None: ...


@dataclass(frozen=True)
class ExecutionBindings:
    """Quantity ledgers and source authorization used in the caller's transaction.

    Token and capacity owners are required. Source-backed model operations
    additionally require the injected operation limiter before their I/O.
    They are runtime assembly objects and never enter a workflow parameter
    or history.
    """

    tokens: TokenLedger
    capacity: CapacityLedger
    extensions: ExtensionOperations | None = None
    sources: RunSourceAuthorization | None = None
    cleanup: ScopeCleanupAuthorization | None = None
    admission_open: Callable[[Session], bool] | None = None
    limit_operation: OperationLimit | None = None

    def __post_init__(self) -> None:
        for ledger, contract in (
            (self.tokens, TokenLedger),
            (self.capacity, CapacityLedger),
        ):
            if not isinstance(ledger, contract):
                raise TypeError(f"Execution requires a complete {contract.__name__}")


@dataclass(frozen=True)
class AdmissionTicket:
    """What one admission held, as serializable references.

    A run persists this ticket when its capacity and tokens are
    reserved; settlement and release resolve the same references later.
    """

    operation_id: str
    token_reservation_id: UUID | None = None
    capacity_reservation_id: UUID | None = None


class RunSourceAuthorization(Protocol):
    def authorize_submission(
        self, session: Session, *, principal_id: UUID, configuration: dict[str, Any]
    ) -> None:
        """Validate API source references against the authenticated submission channel."""
        ...

    def authorize(self, session: Session, *, run_id: UUID) -> None:
        """Reauthorize a frozen configuration with its actual source owner."""
        ...


class ScopeCleanupAuthorization(Protocol):
    def admit_cleanup(
        self,
        session: Session,
        *,
        cleanup_id: UUID,
        subject: ExecutionSubject,
        scope: ExecutionScope,
    ) -> None:
        """Authorize only cleanup of the stored owner's exact scope."""
        ...


@dataclass(frozen=True)
class CleanupScopeAdmission:
    authorization: ScopeCleanupAuthorization
    cleanup_id: UUID

    def admit(self, session: Session, *, subject: ExecutionSubject, scope: ExecutionScope) -> None:
        self.authorization.admit_cleanup(
            session, cleanup_id=self.cleanup_id, subject=subject, scope=scope
        )


class OperationResolution(BaseModel):
    """The confirmed outcome of one external operation.

    Reconciliation applies a resolution to an operation whose effect was
    uncertain; usage is non-negative because a confirmed outcome never
    un-consumes tokens.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(min_length=1, max_length=240)
    result: Any = None
    usage: dict[str, int] | None = None
    storage_bytes: int | None = Field(default=None, ge=0)

    @field_validator("usage")
    @classmethod
    def non_negative_usage(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is not None and any(amount < 0 for amount in value.values()):
            raise ValueError("Confirmed usage must be non-negative")
        return value


class ModelConnections(Protocol):
    def resolve(
        self,
        session: Session,
        *,
        subject: ExecutionSubject,
        scope: ExecutionScope,
        connection_id: UUID,
        model: str,
    ) -> ModelConnection: ...


class RunAssets(Protocol):
    def read_asset(
        self,
        session: Session,
        *,
        subject: ExecutionSubject,
        scope: ExecutionScope,
        ref: str,
        digest: str,
        destination: str,
    ) -> bytes: ...

    def publish_output(
        self,
        session: Session,
        *,
        subject: ExecutionSubject,
        run_id: UUID,
        name: str,
        payload: bytes,
        media_type: str | None,
    ) -> UUID: ...


class ExtensionOperations(Protocol):
    def validate(
        self,
        session: Session,
        *,
        principal_id: UUID,
        scope: ExecutionScope,
        configuration: dict[str, Any],
    ) -> None: ...

    async def invoke(
        self,
        *,
        run_id: UUID,
        operation_id: str,
        reference: dict[str, Any],
        arguments: dict[str, Any],
        kind: str = "operation",
    ) -> dict[str, Any]: ...

    async def hooks(
        self,
        *,
        run_id: UUID,
        occurrence: str,
        stage: str,
        payload: dict[str, Any],
        entered: list[str] | None = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class ConfigurationAccess(Protocol):
    """Configuration-owned authorization and fixed Agent compilation operations."""

    def authorize_scope(
        self,
        session: Session,
        *,
        actor: CurrentUser,
        configuration_id: UUID,
        write: bool,
        manage: bool,
        allow_archived: bool,
    ) -> None: ...

    def flow_definition(
        self,
        session: Session,
        *,
        actor: CurrentUser,
        configuration_id: UUID,
    ) -> tuple[CurrentUser, UUID]: ...

    def authorize_approver(
        self,
        session: Session,
        *,
        approver: CurrentUser,
        owner_id: UUID,
        configuration_id: UUID | None,
    ) -> None: ...

    def validate_agent(
        self,
        session: Session,
        *,
        actor: CurrentUser,
        revision_id: UUID,
        admission: ScopeAdmission,
    ) -> None: ...

    def submit_agent(
        self,
        session: Session,
        *,
        actor: CurrentUser,
        revision_id: UUID,
        input: Any,
        idempotency_key: str,
        initiated_by_id: UUID,
        admission: ScopeAdmission,
        cipher: Any,
        assets: RunAssets,
    ) -> UUID: ...


def configuration_access_for(session: Session) -> ConfigurationAccess:
    value = session.info.get("configuration_access")
    if not isinstance(value, ConfigurationAccess):
        raise RuntimeError("Configuration owner operations are not assembled")
    return value
