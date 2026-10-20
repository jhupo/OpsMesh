"""Channel connection and receipt APIs; vendor protocols live in packages."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.lifecycle import Viewer, authorize_version_read
from opsmesh.capabilities.models import CapabilityVersion
from opsmesh.channels import service
from opsmesh.channels.attachments import authorize_attachments
from opsmesh.channels.contracts import ChannelAdmission
from opsmesh.channels.models import (
    ChannelAccount,
    ChannelDelivery,
    ChannelIdentityMapping,
    ChannelInbox,
    ChannelRoute,
)
from opsmesh.channels.schemas import (
    ChannelAccountCreate,
    ChannelAccountView,
    ChannelAttachment,
    ChannelConnectionAccess,
    ChannelConnectionChange,
    ChannelDeliveryClaim,
    ChannelDeliveryCreate,
    ChannelDeliveryResult,
    ChannelDeliveryView,
    ChannelIdentityMappingCreate,
    ChannelIdentityMappingView,
    ChannelInboxView,
    ChannelMappingStatus,
    ChannelReceiveRequest,
    ChannelReceiveView,
    ChannelReconciliation,
    ChannelRouteCreate,
    ChannelRouteView,
)
from opsmesh.extensions.models import Installation
from opsmesh.identity.auth.dependencies import (
    INSTALLATION_HEADER,
    CurrentUserDep,
    DbSession,
    DelegatedUserDep,
)
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.users.service import load_identity
from opsmesh.orchestration.configurations.service import authorize_configuration
from opsmesh.orchestration.definitions.models import Definition, DefinitionRevision
from opsmesh.orchestration.definitions.schemas import Permission
from opsmesh.shared.audit import AuditActor, AuditRecord, record_audit
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    InvalidArgumentError,
    NotFoundError,
)
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.security.redaction import redact_sensitive_payload, redaction_rules_for
from opsmesh.shared.serialization import canonical_json

router = APIRouter(prefix="/channels", tags=["channels"])


def _cipher(request: Request) -> SecretCipher:
    value = getattr(request.app.state, "secret_cipher", None)
    if not isinstance(value, SecretCipher):
        raise RuntimeError("Channel encryption is unavailable")
    return value


def _configuration_access(
    session: Session, user_id: UUID, configuration_id: UUID, permission: Permission
) -> None:
    actor = load_identity(session, user_id)
    if actor is None:
        raise ForbiddenError("The channel participant is unavailable")
    authorize_configuration(
        session, actor=actor, configuration_id=configuration_id, permission=permission
    )


def _account(
    session: Session, account_id: UUID, user_id: UUID, *, manage: bool = False, write: bool = False
) -> ChannelAccount:
    record: ChannelAccount | None = session.get(ChannelAccount, account_id)
    if record is None:
        raise NotFoundError("Channel account not found")
    if record.configuration_id is not None:
        _configuration_access(
            session,
            user_id,
            record.configuration_id,
            "manage" if manage else "use" if write else "read",
        )
    elif record.owner_id != user_id:
        raise ForbiddenError("The channel account belongs to another user")
    return record


def _account_view(record: ChannelAccount) -> ChannelAccountView:
    return ChannelAccountView.model_validate(record, from_attributes=True)


def _route_view(record: ChannelRoute) -> ChannelRouteView:
    return ChannelRouteView.model_validate(record, from_attributes=True)


def _delegated_account(
    session: Session, account_id: UUID, installation_id: str, user: CurrentUser
) -> ChannelAccount:
    try:
        parsed = UUID(installation_id)
    except ValueError as exc:
        raise ForbiddenError("The delegated installation header is malformed") from exc
    account = session.get(ChannelAccount, account_id)
    if account is None or account.installation_id != parsed:
        raise ForbiddenError("The delegated call does not match the channel account")
    _account(session, account_id, user.user_id, write=True)
    installation = session.get(Installation, parsed)
    if (
        installation is None
        or installation.status != "enabled"
        or installation.version_id != account.version_id
    ):
        raise ForbiddenError("The channel installation is no longer enabled")
    return account


@router.post(
    "/accounts",
    response_model=ChannelAccountView,
    status_code=status.HTTP_201_CREATED,
    operation_id="channels.accounts.create",
)
def create_account(
    payload: ChannelAccountCreate, session: DbSession, user: CurrentUserDep, request: Request
) -> ChannelAccountView:
    if payload.configuration_id is not None:
        _configuration_access(session, user.user_id, payload.configuration_id, "manage")
    version = session.get(CapabilityVersion, payload.version_id)
    installation = session.get(Installation, payload.installation_id)
    if version is None or installation is None or version.status != "published":
        raise NotFoundError("Published channel capability not found")
    if installation.version_id != version.id or installation.status != "enabled":
        raise ConflictError("The channel installation is not enabled")
    capability = authorize_version_read(
        session, version=version, viewer=Viewer(user_id=user.user_id)
    )
    if capability.type != "channel" or capability.status != "active":
        raise InvalidArgumentError("The capability is not a channel")
    with session_transaction(session):
        if (
            redact_sensitive_payload(payload.configuration, rules=redaction_rules_for(session))
            != payload.configuration
        ):
            raise InvalidArgumentError("Sensitive channel configuration is not supported")
        record = ChannelAccount(
            owner_id=user.user_id,
            configuration_id=payload.configuration_id,
            channel_type=payload.channel_type,
            external_account_id=payload.external_account_id,
            installation_id=payload.installation_id,
            version_id=payload.version_id,
            configuration=payload.configuration,
        )
        session.add(record)
        session.flush()
        record_audit(
            session,
            AuditRecord(
                category="operation",
                action="channel.account_created",
                actor=AuditActor.from_user(user),
                source="platform",
                target_type="channel_account",
                target_id=str(record.id),
                summary="Created a channel account",
                payload_json=canonical_json(
                    {"installation_id": str(record.installation_id), "epoch": record.epoch}
                ),
            ),
        )
    return _account_view(record)


@router.post(
    "/accounts/{account_id}/connection",
    response_model=ChannelAccountView,
    operation_id="channels.accounts.connection.change",
)
def change_connection(
    account_id: UUID,
    payload: ChannelConnectionChange,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> ChannelAccountView:
    _account(session, account_id, user.user_id, manage=True)
    with session_transaction(session):
        account = service.change_connection(
            session,
            account_id=account_id,
            expected_epoch=payload.expected_epoch,
            enabled=payload.enabled,
        )
        record_audit(
            session,
            AuditRecord(
                category="operation",
                action="channel.connection_changed",
                actor=AuditActor.from_user(user),
                source="platform",
                target_type="channel_account",
                target_id=str(account.id),
                summary="Changed a channel connection",
                payload_json=canonical_json({"epoch": account.epoch, "status": account.status}),
            ),
        )
    return _account_view(account)


@router.get(
    "/accounts/{account_id}/connection",
    response_model=ChannelConnectionAccess,
    operation_id="channels.accounts.connection.read",
)
def connection_access(
    account_id: UUID,
    session: DbSession,
    delegated: DelegatedUserDep,
    request: Request,
    expected_epoch: int = Query(ge=1),
    x_installation_id: str = Header(alias=INSTALLATION_HEADER),
) -> ChannelConnectionAccess:
    account = _delegated_account(session, account_id, x_installation_id, delegated)
    if account.status != "enabled" or account.epoch != expected_epoch:
        raise ConflictError("The channel connection epoch is no longer enabled")
    with session_transaction(session):
        record_audit(
            session,
            AuditRecord(
                category="operation",
                action="channel.connection_accessed",
                actor=AuditActor.from_user(delegated),
                source="platform",
                target_type="channel_account",
                target_id=str(account.id),
                summary="Channel adapter accessed its connection",
                payload_json=canonical_json({"epoch": account.epoch}),
            ),
        )
    return ChannelConnectionAccess(
        account_id=account.id,
        epoch=account.epoch,
        configuration=account.configuration,
    )


@router.get(
    "/accounts", response_model=list[ChannelAccountView], operation_id="channels.accounts.list"
)
def list_accounts(
    session: DbSession,
    user: CurrentUserDep,
    after: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    configuration_id: UUID | None = None,
) -> list[ChannelAccountView]:
    """List personal accounts by default, or accounts in one authorized exact scope."""
    query = select(ChannelAccount)
    if configuration_id is not None:
        _configuration_access(session, user.user_id, configuration_id, "read")
        query = query.where(ChannelAccount.configuration_id == configuration_id)
    else:
        query = query.where(
            ChannelAccount.owner_id == user.user_id,
            ChannelAccount.configuration_id.is_(None),
        )
    if after:
        query = query.where(ChannelAccount.id > after)
    records = session.scalars(query.order_by(ChannelAccount.id).limit(limit))
    return [_account_view(record) for record in records]


@router.get(
    "/accounts/{account_id}",
    response_model=ChannelAccountView,
    operation_id="channels.accounts.get",
)
def get_account(account_id: UUID, session: DbSession, user: CurrentUserDep) -> ChannelAccountView:
    return _account_view(_account(session, account_id, user.user_id))


@router.post(
    "/accounts/{account_id}/mappings",
    response_model=ChannelIdentityMappingView,
    status_code=status.HTTP_201_CREATED,
    operation_id="channels.accounts.mappings.create",
)
def create_mapping(
    account_id: UUID,
    payload: ChannelIdentityMappingCreate,
    session: DbSession,
    user: CurrentUserDep,
) -> ChannelIdentityMappingView:
    account = _account(session, account_id, user.user_id, manage=True)
    if account.configuration_id is not None:
        _configuration_access(session, payload.user_id, account.configuration_id, "use")
    with session_transaction(session):
        session.refresh(account, with_for_update=True)
        existing = session.scalar(
            select(ChannelIdentityMapping).where(
                ChannelIdentityMapping.account_id == account_id,
                ChannelIdentityMapping.external_identity == payload.external_identity,
            )
        )
        if existing is not None:
            if existing.user_id != payload.user_id:
                raise ConflictError("The external identity is already mapped to another user")
            return ChannelIdentityMappingView.model_validate(existing, from_attributes=True)
        record = ChannelIdentityMapping(account_id=account_id, **payload.model_dump())
        session.add(record)
        session.flush()
        record_audit(
            session,
            AuditRecord(
                category="operation",
                action="channel.identity_mapping_created",
                actor=AuditActor.from_user(user),
                source="platform",
                target_type="channel_mapping",
                target_id=str(record.id),
                summary="Created a channel identity mapping",
                payload_json=canonical_json(
                    {"account_id": str(account_id), "user_id": str(record.user_id)}
                ),
            ),
        )
    return ChannelIdentityMappingView.model_validate(record, from_attributes=True)


@router.get(
    "/accounts/{account_id}/mappings",
    response_model=list[ChannelIdentityMappingView],
    operation_id="channels.accounts.mappings.list",
)
def list_mappings(
    account_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    after: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[ChannelIdentityMappingView]:
    _account(session, account_id, user.user_id, manage=True)
    query = select(ChannelIdentityMapping).where(ChannelIdentityMapping.account_id == account_id)
    if after:
        query = query.where(ChannelIdentityMapping.id > after)
    return [
        ChannelIdentityMappingView.model_validate(item, from_attributes=True)
        for item in session.scalars(query.order_by(ChannelIdentityMapping.id).limit(limit))
    ]


@router.put(
    "/accounts/{account_id}/mappings/{mapping_id}",
    response_model=ChannelIdentityMappingView,
    operation_id="channels.accounts.mappings.update",
)
def change_mapping(
    account_id: UUID,
    mapping_id: UUID,
    payload: ChannelMappingStatus,
    session: DbSession,
    user: CurrentUserDep,
) -> ChannelIdentityMappingView:
    account = _account(session, account_id, user.user_id, manage=True)
    with session_transaction(session):
        session.refresh(account, with_for_update=True)
        record = session.get(ChannelIdentityMapping, mapping_id)
        if record is None or record.account_id != account_id:
            raise NotFoundError("Channel identity mapping not found")
        record.status = payload.status
        record_audit(
            session,
            AuditRecord(
                category="operation",
                action="channel.identity_mapping_changed",
                actor=AuditActor.from_user(user),
                source="platform",
                target_type="channel_mapping",
                target_id=str(record.id),
                summary="Channel identity mapping changed",
                payload_json=canonical_json({"status": record.status}),
            ),
        )
    return ChannelIdentityMappingView.model_validate(record, from_attributes=True)


@router.post(
    "/accounts/{account_id}/routes",
    response_model=ChannelRouteView,
    status_code=status.HTTP_201_CREATED,
    operation_id="channels.accounts.routes.create",
)
def create_route(
    account_id: UUID, payload: ChannelRouteCreate, session: DbSession, user: CurrentUserDep
) -> ChannelRouteView:
    account = _account(session, account_id, user.user_id, manage=True)
    if (
        account.configuration_id is not None
        and account.configuration_id != payload.configuration_id
    ):
        raise InvalidArgumentError("The route scope must match the channel account scope")
    configuration = authorize_configuration(
        session, actor=user, configuration_id=payload.configuration_id, permission="manage"
    )
    revision = session.get(DefinitionRevision, payload.flow_revision_id)
    if revision is None:
        raise NotFoundError("Flow revision not found")
    definition = session.get(Definition, revision.definition_id)
    if (
        definition is None
        or definition.kind != "flow"
        or configuration.definition_revision_id != revision.id
    ):
        raise InvalidArgumentError("The flow revision is outside the route scope")
    with session_transaction(session):
        session.refresh(account, with_for_update=True)
        if (
            session.scalar(
                select(ChannelRoute.id).where(
                    ChannelRoute.account_id == account_id,
                    ChannelRoute.conversation_key == payload.conversation_key,
                )
            )
            is not None
        ):
            raise ConflictError("The channel conversation already has a route")
        record = ChannelRoute(account_id=account_id, **payload.model_dump())
        session.add(record)
        session.flush()
        record_audit(
            session,
            AuditRecord(
                category="operation",
                action="channel.route_created",
                actor=AuditActor.from_user(user),
                source="platform",
                target_type="channel_route",
                target_id=str(record.id),
                summary="Created a channel route",
                payload_json=canonical_json({"account_id": str(account_id)}),
            ),
        )
    return _route_view(record)


@router.get(
    "/accounts/{account_id}/routes",
    response_model=list[ChannelRouteView],
    operation_id="channels.accounts.routes.list",
)
def list_routes(
    account_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    after: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[ChannelRouteView]:
    _account(session, account_id, user.user_id)
    query = select(ChannelRoute).where(ChannelRoute.account_id == account_id)
    if after:
        query = query.where(ChannelRoute.id > after)
    return [
        _route_view(item) for item in session.scalars(query.order_by(ChannelRoute.id).limit(limit))
    ]


@router.delete(
    "/accounts/{account_id}/routes/{route_id}",
    status_code=204,
    operation_id="channels.accounts.routes.delete",
)
def delete_route(
    account_id: UUID, route_id: UUID, session: DbSession, user: CurrentUserDep
) -> None:
    account = _account(session, account_id, user.user_id, manage=True)
    with session_transaction(session):
        session.refresh(account, with_for_update=True)
        route = session.get(ChannelRoute, route_id)
        if route is None or route.account_id != account_id:
            raise NotFoundError("Channel route not found")
        session.delete(route)
        record_audit(
            session,
            AuditRecord(
                category="operation",
                action="channel.route_deleted",
                actor=AuditActor.from_user(user),
                source="platform",
                target_type="channel_route",
                target_id=str(route.id),
                summary="Deleted a channel route",
                payload_json=canonical_json({"account_id": str(account_id)}),
            ),
        )


@router.post(
    "/accounts/{account_id}/inbox",
    response_model=ChannelReceiveView,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="channels.accounts.inbox.receive",
)
def receive(
    account_id: UUID,
    payload: ChannelReceiveRequest,
    session: DbSession,
    delegated: DelegatedUserDep,
    request: Request,
    x_installation_id: str = Header(alias=INSTALLATION_HEADER),
) -> ChannelReceiveView:
    account = _delegated_account(session, account_id, x_installation_id, delegated)
    with session_transaction(session):
        inbox, accepted = service.persist_inbox(
            session,
            account_id=account.id,
            epoch=payload.epoch,
            external_message_id=payload.external_message_id,
            conversation_key=payload.conversation_key,
            payload={
                **payload.payload,
                "external_identity": payload.external_identity,
                "attachments": [item.model_dump(mode="json") for item in payload.attachments],
            },
            cipher=_cipher(request),
        )
        if accepted:
            mapping = session.scalar(
                select(ChannelIdentityMapping).where(
                    ChannelIdentityMapping.account_id == account.id,
                    ChannelIdentityMapping.external_identity == payload.external_identity,
                    ChannelIdentityMapping.status == "active",
                )
            )
            mapped_actor = load_identity(session, mapping.user_id) if mapping else None
            if mapped_actor is None or mapped_actor.status != "active":
                inbox.status = "rejected"
                inbox.failure_code = "identity_unmapped"
                return ChannelReceiveView(inbox_id=inbox.id, accepted=True, status=inbox.status)
            try:
                with session.begin_nested():
                    authorize_attachments(
                        session, user_id=mapped_actor.user_id, attachments=payload.attachments
                    )
                    service.route_inbox(
                        session,
                        inbox=inbox,
                        actor=mapped_actor,
                        cipher=_cipher(request),
                        start_flow=getattr(request.app.state, "channel_flow_starter", None),
                    )
            except DomainError as exc:
                inbox.status, inbox.failure_code = "rejected", exc.code
    return ChannelReceiveView(inbox_id=inbox.id, accepted=accepted, status=inbox.status)


@router.post(
    "/accounts/{account_id}/deliveries",
    response_model=ChannelDeliveryView,
    status_code=status.HTTP_201_CREATED,
    operation_id="channels.accounts.deliveries.create",
)
def create_delivery(
    account_id: UUID,
    payload: ChannelDeliveryCreate,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> ChannelDeliveryView:
    account = _account(session, account_id, user.user_id, write=True)
    with session_transaction(session):
        authorize_attachments(session, user_id=user.user_id, attachments=payload.attachments)
        record = service.reserve_delivery(
            session,
            account_id=account.id,
            idempotency_key=payload.idempotency_key,
            conversation_key=payload.conversation_key,
            payload={
                **payload.payload,
                "attachments": [item.model_dump(mode="json") for item in payload.attachments],
            },
            cipher=_cipher(request),
        )
    return ChannelDeliveryView.model_validate(record, from_attributes=True)


@router.post(
    "/deliveries/{delivery_id}/claim",
    response_model=ChannelDeliveryClaim,
    operation_id="channels.deliveries.claim",
)
def claim_delivery(
    delivery_id: UUID,
    session: DbSession,
    delegated: DelegatedUserDep,
    request: Request,
    x_installation_id: str = Header(alias=INSTALLATION_HEADER),
) -> ChannelDeliveryClaim:
    delivery = session.get(ChannelDelivery, delivery_id)
    if delivery is None:
        raise NotFoundError("Channel delivery not found")
    _delegated_account(session, delivery.account_id, x_installation_id, delegated)
    with session_transaction(session):
        admission = getattr(request.app.state, "channel_admission", None)
        if not callable(admission):
            raise DomainError("Channel admission is unavailable", status_code=503)
        if not cast(ChannelAdmission, admission)(session):
            raise ConflictError("Channel sending admission is closed")
        record = service.claim_delivery(session, delivery_id=delivery_id)
        content = (
            _cipher(request).decrypt_payload(
                record.payload_ciphertext, key_id=record.payload_key_id
            )
            if record.status == "sending"
            else {}
        )
        refs = content.get("attachments", [])
        if not isinstance(refs, list):
            raise InvalidArgumentError("Invalid stored channel attachments")
        authorize_attachments(
            session,
            user_id=delegated.user_id,
            attachments=[ChannelAttachment.model_validate(item) for item in refs],
        )
    return ChannelDeliveryClaim(
        **ChannelDeliveryView.model_validate(record, from_attributes=True).model_dump(),
        payload=content,
    )


@router.post(
    "/deliveries/{delivery_id}/result",
    response_model=ChannelDeliveryView,
    operation_id="channels.deliveries.result",
)
def delivery_result(
    delivery_id: UUID,
    payload: ChannelDeliveryResult,
    session: DbSession,
    delegated: DelegatedUserDep,
    x_installation_id: str = Header(alias=INSTALLATION_HEADER),
) -> ChannelDeliveryView:
    delivery = session.get(ChannelDelivery, delivery_id)
    if delivery is None:
        raise NotFoundError("Channel delivery not found")
    _delegated_account(session, delivery.account_id, x_installation_id, delegated)
    with session_transaction(session):
        record = service.record_delivery_result(
            session,
            delivery_id=delivery_id,
            status=payload.status,
            external_message_id=payload.external_message_id,
            failure_code=payload.failure_code,
        )
    return ChannelDeliveryView.model_validate(record, from_attributes=True)


@router.post(
    "/deliveries/{delivery_id}/reconcile",
    response_model=ChannelDeliveryView,
    operation_id="channels.deliveries.reconcile",
)
def reconcile_delivery(
    delivery_id: UUID, payload: ChannelReconciliation, session: DbSession, user: CurrentUserDep
) -> ChannelDeliveryView:
    delivery = session.get(ChannelDelivery, delivery_id)
    if delivery is None:
        raise NotFoundError("Channel delivery not found")
    _account(session, delivery.account_id, user.user_id, manage=True)
    with session_transaction(session):
        delivery, changed = service.reconcile_delivery(
            session,
            delivery_id=delivery_id,
            status=payload.status,
            evidence=payload.evidence,
            actor_id=user.user_id,
            external_message_id=payload.external_message_id,
        )
        if changed:
            record_audit(
                session,
                AuditRecord(
                    category="operation",
                    action="channel.delivery_reconciled",
                    actor=AuditActor.from_user(user),
                    source="platform",
                    target_type="channel_delivery",
                    target_id=str(delivery.id),
                    summary="Channel send reconciliation recorded",
                    payload_json=canonical_json({"status": payload.status}),
                ),
            )
    return ChannelDeliveryView.model_validate(delivery, from_attributes=True)


@router.get(
    "/accounts/{account_id}/inbox",
    response_model=list[ChannelInboxView],
    operation_id="channels.accounts.inbox.list",
)
def list_inbox(
    account_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    after: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[ChannelInboxView]:
    _account(session, account_id, user.user_id)
    query = select(ChannelInbox).where(ChannelInbox.account_id == account_id)
    if after:
        query = query.where(ChannelInbox.id > after)
    records = session.scalars(query.order_by(ChannelInbox.id).limit(limit))
    return [ChannelInboxView.model_validate(record, from_attributes=True) for record in records]


@router.get(
    "/accounts/{account_id}/deliveries",
    response_model=list[ChannelDeliveryView],
    operation_id="channels.accounts.deliveries.list",
)
def list_deliveries(
    account_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    after: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[ChannelDeliveryView]:
    _account(session, account_id, user.user_id)
    query = select(ChannelDelivery).where(ChannelDelivery.account_id == account_id)
    if after:
        query = query.where(ChannelDelivery.id > after)
    records = session.scalars(query.order_by(ChannelDelivery.id).limit(limit))
    return [ChannelDeliveryView.model_validate(record, from_attributes=True) for record in records]
