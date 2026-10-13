"""Authenticated remote connector ingress and delivery receipt endpoints."""

import json
from collections.abc import AsyncIterator
from contextlib import closing
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from opsmesh_plugin_sdk.messaging.contracts import (
    AcceptedEvent,
    AttachmentUpload,
    ChannelEvent,
    MessageAttachment,
    ReplyReceipt,
    ReplyReceiptRequest,
)
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.requests import Request

from opsmesh.capabilities.plugins.authentication import PluginAuthenticationService, PluginPrincipal
from opsmesh.governance.security_events.service import SecurityEventRecorder
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.messaging.channels.service import ChannelService
from opsmesh.orchestration.automations.plugin_attachments import PluginAttachmentService
from opsmesh.resources.files.service import WorkspaceFileService
from opsmesh.resources.storage.backends import create_storage
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.client_ip import security_request_context

router = APIRouter(
    prefix="/plugin-services/{workspace_id}/{install_id}/channels", tags=["channels"]
)


async def channel_principal(
    request: Request,
    workspace_id: UUID,
    install_id: UUID,
    authorization: str = Header(default=""),
    session: Session = Depends(get_db_session),
) -> AsyncIterator[PluginPrincipal]:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid plugin credentials"
        )
    try:
        yield PluginAuthenticationService(session).authenticate(workspace_id, install_id, token)
    except ResourceAccessDeniedError as exc:
        session.rollback()
        SecurityEventRecorder(session).record_request_event(
            request_context=security_request_context(request),
            action="auth.plugin.channel_rejected",
            outcome="denied",
            severity="warning",
            reason="Channel plugin access denied",
            metadata={"install_id": str(install_id)},
        )
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Channel access denied"
        ) from exc


@router.post("/{connection_id}/attachments", response_model=MessageAttachment, status_code=201)
async def upload_attachment(
    connection_id: UUID,
    metadata: str = Form(max_length=16000),
    file: UploadFile = File(),
    principal: PluginPrincipal = Depends(channel_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MessageAttachment:
    connection = ChannelService(session).require_principal(
        principal, connection_id, "attachments.write"
    )
    # Routing metadata is not included in the model projection.
    try:
        value = json.loads(metadata)
        if not isinstance(value, dict):
            raise ValueError("Attachment metadata must be an object")
        conversation_id = value.pop("conversation_id", None)
        if not isinstance(conversation_id, str) or not 1 <= len(conversation_id) <= 160:
            raise ValueError("Attachment metadata requires conversation_id")
        request = AttachmentUpload.model_validate(value)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, "Invalid attachment metadata") from exc
    automation = ChannelService(session).route(connection, conversation_id, request.sender_id)
    limit = min(settings.max_upload_bytes, 20 * 1024 * 1024)
    try:
        content = await file.read(limit + 1)
    finally:
        await file.close()
    if len(content) > limit:
        raise HTTPException(413, "Attachment exceeds upload limit")
    with closing(create_storage(settings)) as storage:
        reference = PluginAttachmentService(session).upload(
            principal,
            automation.id,
            request,
            content,
            WorkspaceFileService(session, storage, limit),
        )
        session.commit()
        return reference


@router.post("/{connection_id}/events", status_code=202, response_model=AcceptedEvent)
def receive_event(
    connection_id: UUID,
    envelope: ChannelEvent,
    principal: PluginPrincipal = Depends(channel_principal),
    session: Session = Depends(get_db_session),
) -> AcceptedEvent:
    try:
        event = ChannelService(session).receive(principal, connection_id, envelope)
        session.commit()
        return AcceptedEvent.model_validate(event)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Invalid channel event") from exc


@router.post("/{connection_id}/deliveries/{delivery_id}/receipt", response_model=ReplyReceipt)
def receipt(
    connection_id: UUID,
    delivery_id: UUID,
    request: ReplyReceiptRequest,
    principal: PluginPrincipal = Depends(channel_principal),
    session: Session = Depends(get_db_session),
) -> ReplyReceipt:
    from sqlalchemy import select

    from opsmesh.messaging.channels.models import ChannelDelivery

    ChannelService(session).require_principal(principal, connection_id, "messages.receive")
    if request.delivery_id != delivery_id:
        raise HTTPException(status_code=400, detail="Delivery identifiers differ")
    delivery = session.scalar(
        select(ChannelDelivery)
        .where(
            ChannelDelivery.workspace_id == principal.workspace_id,
            ChannelDelivery.connection_id == connection_id,
            ChannelDelivery.id == delivery_id,
        )
        .with_for_update()
    )
    if delivery is None:
        raise HTTPException(status_code=404, detail="Delivery not found")
    if delivery.status in {"delivered", "failed"} and delivery.status != request.status:
        raise HTTPException(status_code=409, detail="Delivery is already terminal")
    from opsmesh.messaging.channels.delivery import record_receipt

    record_receipt(session, delivery, request)
    session.commit()
    return ReplyReceipt(
        delivery_id=delivery.id,
        status=request.status,
        provider_message_id=delivery.provider_message_id,
        error_code=delivery.error_code,
    )
