"""Encrypted configuration persistence; no transport or Agent lifecycle."""

from collections.abc import Callable
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.gateway.contracts import ChannelDefinition
from opsmesh.gateway.storage.models import (
    GatewayChannel,
    GatewayConversationEvent,
    GatewayDelivery,
    GatewayInbox,
    GatewayManagementOperation,
)
from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError
from opsmesh.shared.security.encryption import SecretCipher


class GatewayStore:
    def __init__(self, sessions: sessionmaker[Session], cipher: SecretCipher) -> None:
        self.sessions, self.cipher = sessions, cipher

    def definition(self, row: GatewayChannel) -> ChannelDefinition:
        settings = self.cipher.decrypt_payload(row.encrypted_settings, key_id=row.key_id)
        return ChannelDefinition(
            id=row.id,
            owner_id=row.owner_id,
            kind=row.kind,
            name=row.name,
            enabled=row.enabled,
            revision=row.revision,
            binding_revision=row.binding_revision,
            target=settings.get("target"),
            config=settings.get("config", {}),
            policy=settings.get("policy", {}),
        )

    def audit(self, session: Session, action: str, actor_id: UUID, target_id: UUID) -> None:
        writer = session.info.get("gateway_audit")
        if writer is None:
            raise RuntimeError("The gateway audit writer is not assembled")
        cast(Callable[[Session, str, UUID, UUID], None], writer)(
            session,
            action,
            actor_id,
            target_id,
        )

    def list_channels(self) -> list[ChannelDefinition]:
        with self.sessions() as session:
            return [
                self.definition(row)
                for row in session.scalars(
                    select(GatewayChannel)
                    .where(GatewayChannel.deleted.is_(False))
                    .order_by(GatewayChannel.id)
                )
            ]

    def get(self, channel_id: UUID) -> ChannelDefinition:
        with self.sessions() as session:
            row = session.get(GatewayChannel, channel_id)
            if row is None or row.deleted:
                raise NotFoundError("Channel does not exist")
            return self.definition(row)

    def save(self, definition: ChannelDefinition, actor_id: UUID) -> ChannelDefinition:
        with self.sessions.begin() as session:
            return self.save_in(session, definition, actor_id)

    def save_in(
        self, session: Session, definition: ChannelDefinition, actor_id: UUID
    ) -> ChannelDefinition:
        encrypted = self.cipher.encrypt_payload(
            {
                "config": definition.config,
                "policy": definition.policy.model_dump(mode="json"),
                "target": definition.target.model_dump(mode="json") if definition.target else None,
            }
        )
        row = session.get(GatewayChannel, definition.id, with_for_update=True)
        if row is None:
            row = GatewayChannel(
                id=definition.id,
                owner_id=definition.owner_id,
                kind=definition.kind,
                revision=1,
                binding_revision=1,
            )
            session.add(row)
        else:
            if row.deleted:
                raise NotFoundError("Channel was deleted")
            if row.owner_id != definition.owner_id:
                raise ForbiddenError("Channel ownership cannot be changed")
            if row.kind != definition.kind or row.revision != definition.revision:
                raise ConflictError("Channel kind or revision changed")
            if self.definition(row).target != definition.target:
                if (
                    session.scalar(
                        select(GatewayInbox.id)
                        .where(
                            GatewayInbox.channel_id == row.id,
                            GatewayInbox.status.in_(
                                (
                                    "pending",
                                    "ready",
                                    "dispatching",
                                    "submitted",
                                    "responding",
                                    "batched",
                                )
                            ),
                        )
                        .limit(1)
                    )
                    or session.scalar(
                        select(GatewayDelivery.id)
                        .where(
                            GatewayDelivery.channel_id == row.id,
                            GatewayDelivery.status.in_(("pending", "sending", "uncertain")),
                        )
                        .limit(1)
                    )
                    or session.scalar(
                        select(GatewayManagementOperation.id)
                        .where(
                            GatewayManagementOperation.channel_id == row.id,
                            GatewayManagementOperation.status.in_(
                                ("awaiting_approval", "pending", "executing", "uncertain")
                            ),
                        )
                        .limit(1)
                    )
                    or session.scalar(
                        select(GatewayConversationEvent.id)
                        .where(
                            GatewayConversationEvent.channel_id == row.id,
                            GatewayConversationEvent.status == "pending",
                        )
                        .limit(1)
                    )
                ):
                    raise ConflictError("Drain active messages before changing the channel target")
                row.binding_revision += 1
            row.revision += 1
        row.name, row.enabled = definition.name, definition.enabled
        row.encrypted_settings, row.key_id = encrypted.ciphertext, encrypted.key_id
        session.flush()
        self.audit(session, "channel.save", actor_id, row.id)
        return self.definition(row)

    def delete(self, channel_id: UUID, actor_id: UUID) -> None:
        # Explicit soft deletion retains bindings, pending deliveries and audit evidence.
        with self.sessions.begin() as session:
            row = session.get(GatewayChannel, channel_id, with_for_update=True)
            if row is None or row.deleted:
                raise NotFoundError("Channel does not exist")
            row.enabled = False
            row.deleted = True
            row.revision += 1
            self.audit(session, "channel.delete", actor_id, channel_id)
