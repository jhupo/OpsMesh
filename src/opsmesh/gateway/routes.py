from typing import Any

from fastapi import APIRouter, Depends, Request
from opsmesh_sdk import UserIdentity
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.agents.models import AgentInstance
from opsmesh.plugins.loader import load
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.shared.http import get_application, get_current_user, get_db_session, serialize_record
from opsmesh.users.service import identity, require_admin

from .adapters.config import validate_config
from .models import (
    ChannelConfiguration,
    ChannelEventRecord,
    ChannelIdentity,
    ChannelOperation,
    OutboundDelivery,
)
from .operations import enqueue
from .schemas import (
    BindChannelIdentityRequest,
    ChannelEventDetail,
    ChannelEventView,
    ChannelIdentityView,
    ChannelView,
    CreateChannelRequest,
    CreateOperationRequest,
    DeliveryDetail,
    DeliveryView,
    OperationDetail,
    OperationView,
    UpdateChannelRequest,
)

router = APIRouter(tags=["gateway"])


@router.post("/channels", status_code=201, response_model=ChannelView)
def create_channel(
    body: CreateChannelRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    require_admin(user)
    app = get_application(request)
    if db.get(AgentInstance, body.agent_id) is None:
        raise NotFoundError("Agent not found")
    if body.plugin_id:
        plugin = load(db, app.settings.data_dir, app.registry, body.plugin_id)
        if body.adapter not in plugin.channels:
            raise NotFoundError("Plugin channel adapter not found")
    elif body.adapter not in {"qq", "dingtalk"}:
        raise ValueError("A custom adapter requires a plugin")
    row = ChannelConfiguration(
        owner_id=user.user_id,
        name=body.name,
        adapter=body.adapter,
        plugin_id=body.plugin_id,
        agent_id=body.agent_id,
        encrypted_config=app.cipher.encrypt(
            body.config if body.plugin_id else validate_config(body.adapter, body.config)
        ),
    )
    db.add(row)
    db.flush()
    return serialize_record(row)


@router.get("/channels", response_model=list[ChannelView])
def list_channels(
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict[str, Any]]:
    require_admin(user)
    return [serialize_record(row) for row in db.scalars(select(ChannelConfiguration).limit(200))]


@router.patch("/channels/{channel_id}", response_model=ChannelView)
def update_channel(
    channel_id: str,
    body: UpdateChannelRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    require_admin(user)
    row = db.get(ChannelConfiguration, channel_id, with_for_update=True)
    if row is None:
        raise NotFoundError("Channel not found")
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.config is not None:
        row.encrypted_config = get_application(request).cipher.encrypt(
            body.config if row.plugin_id else validate_config(row.adapter, body.config)
        )
    row.revision += 1
    return serialize_record(row)


@router.get("/channels/{channel_id}/capabilities")
def capabilities(
    channel_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    require_admin(user)
    channel = db.get(ChannelConfiguration, channel_id)
    if channel is None:
        raise NotFoundError("Channel not found")
    return channel.capabilities


@router.post("/channels/{channel_id}/operations", status_code=202, response_model=OperationView)
def create_operation(
    channel_id: str,
    body: CreateOperationRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    require_admin(user)
    row = enqueue(
        db,
        get_application(request).cipher,
        channel_id,
        user.user_id,
        body.operation,
        body.parameters,
        body.idempotency_key,
    )
    return serialize_record(row)


@router.get("/channels/{channel_id}/operations", response_model=list[OperationView])
def list_operations(
    channel_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict[str, Any]]:
    require_admin(user)
    return [
        serialize_record(row)
        for row in db.scalars(
            select(ChannelOperation)
            .where(ChannelOperation.channel_id == channel_id)
            .order_by(ChannelOperation.created_at.desc())
            .limit(200)
        )
    ]


@router.get("/channels/{channel_id}/operations/{operation_id}", response_model=OperationDetail)
def operation_detail(
    channel_id: str,
    operation_id: str,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    require_admin(user)
    row = db.get(ChannelOperation, operation_id)
    if row is None or row.channel_id != channel_id:
        raise NotFoundError("Channel operation not found")
    result = serialize_record(row)
    result["result"] = (
        get_application(request).cipher.decrypt(row.encrypted_result).get("value")
        if row.encrypted_result
        else None
    )
    return result


@router.post("/channels/{channel_id}/operations/{operation_id}/retry", response_model=OperationView)
def retry_operation(
    channel_id: str,
    operation_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    require_admin(user)
    channel = db.get(ChannelConfiguration, channel_id, with_for_update=True)
    row = db.get(ChannelOperation, operation_id, with_for_update=True)
    if channel is None or row is None or row.channel_id != channel_id:
        raise NotFoundError("Channel operation not found")
    if not channel.enabled or row.status not in {"failed", "uncertain"}:
        raise ConflictError(
            "Only failed or uncertain operations on an enabled channel can be retried"
        )
    row.status, row.error_code, row.finished_at = "pending", None, None
    return serialize_record(row)


@router.get("/channels/{channel_id}/events", response_model=list[ChannelEventView])
def list_events(
    channel_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict[str, Any]]:
    require_admin(user)
    return [
        serialize_record(row)
        for row in db.scalars(
            select(ChannelEventRecord)
            .where(ChannelEventRecord.channel_id == channel_id)
            .order_by(ChannelEventRecord.created_at.desc())
            .limit(200)
        )
    ]


@router.get("/channels/{channel_id}/events/{event_id}", response_model=ChannelEventDetail)
def event_detail(
    channel_id: str,
    event_id: str,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    require_admin(user)
    row = db.get(ChannelEventRecord, event_id)
    if row is None or row.channel_id != channel_id:
        raise NotFoundError("Channel event not found")
    result = serialize_record(row)
    result["payload"] = get_application(request).cipher.decrypt(row.encrypted_event)
    return result


@router.get("/channels/{channel_id}/identities", response_model=list[ChannelIdentityView])
def list_identities(
    channel_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict[str, Any]]:
    require_admin(user)
    return [
        serialize_record(row)
        for row in db.scalars(
            select(ChannelIdentity).where(ChannelIdentity.channel_id == channel_id).limit(200)
        )
    ]


@router.put("/channels/{channel_id}/identities/{external_user_id}", status_code=204)
def bind_identity(
    channel_id: str,
    external_user_id: str,
    body: BindChannelIdentityRequest,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> None:
    require_admin(user)
    identity(db, body.user_id)
    if (
        not external_user_id
        or len(external_user_id) > 200
        or db.get(ChannelConfiguration, channel_id) is None
    ):
        raise NotFoundError("Channel or external identity not found")
    row = db.get(ChannelIdentity, (channel_id, external_user_id))
    if row is None:
        db.add(
            ChannelIdentity(
                channel_id=channel_id, external_user_id=external_user_id, user_id=body.user_id
            )
        )
    else:
        row.user_id = body.user_id


@router.delete("/channels/{channel_id}/identities/{external_user_id}", status_code=204)
def unbind_identity(
    channel_id: str,
    external_user_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> None:
    require_admin(user)
    row = db.get(ChannelIdentity, (channel_id, external_user_id))
    if row is not None:
        db.delete(row)


@router.get("/channels/{channel_id}/deliveries", response_model=list[DeliveryView])
def list_deliveries(
    channel_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict[str, Any]]:
    require_admin(user)
    return [
        serialize_record(row)
        for row in db.scalars(
            select(OutboundDelivery)
            .where(OutboundDelivery.channel_id == channel_id)
            .order_by(OutboundDelivery.created_at.desc())
            .limit(200)
        )
    ]


@router.get("/deliveries/{delivery_id}", response_model=DeliveryDetail)
def delivery_detail(
    delivery_id: str,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    require_admin(user)
    row = db.get(OutboundDelivery, delivery_id)
    if row is None:
        raise NotFoundError("Delivery not found")
    result = serialize_record(row)
    result["receipt"] = (
        get_application(request).cipher.decrypt(row.encrypted_receipt).get("value")
        if row.encrypted_receipt
        else None
    )
    return result


@router.post("/deliveries/{delivery_id}/retry", response_model=DeliveryView)
def retry_delivery(
    delivery_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    require_admin(user)
    row = db.get(OutboundDelivery, delivery_id, with_for_update=True)
    if row is None:
        raise NotFoundError("Delivery not found")
    if row.status not in {"failed", "uncertain"}:
        raise ConflictError("Only failed or uncertain deliveries can be retried")
    row.status, row.error_code = "pending", None
    return serialize_record(row)
