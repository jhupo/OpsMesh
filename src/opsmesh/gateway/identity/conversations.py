"""Observed chats and membership facts are never platform authorization."""

from __future__ import annotations

import builtins
from uuid import UUID

from opsmesh_sdk.channels import InboundMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.gateway.storage.models import GatewayConversation, GatewayMember
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.shared.serialization import payload_digest


def conversation_key(message: InboundMessage) -> str:
    return payload_digest(
        {
            "scope": message.actor.scope,
            "kind": message.conversation.kind,
            "id": message.conversation.external_id,
        }
    )


class Conversations:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store

    def observe(
        self, session: Session, channel_id: UUID, identity_id: UUID, message: InboundMessage
    ) -> None:
        key = conversation_key(message)
        row = session.get(GatewayConversation, (channel_id, key))
        encrypted = self.store.cipher.encrypt_payload(message.conversation.model_dump(mode="json"))
        if row is None:
            row = GatewayConversation(
                channel_id=channel_id,
                conversation_key=key,
                external_id=message.conversation.external_id,
                kind=message.conversation.kind,
            )
            session.add(row)
        row.encrypted_profile, row.key_id = encrypted.ciphertext, encrypted.key_id
        member = session.get(GatewayMember, (identity_id, key))
        if member is None:
            member = GatewayMember(identity_id=identity_id, conversation_key=key)
            session.add(member)
        member.external_role = message.actor.external_role
        member.source = "message"

    def list(self, channel_id: UUID) -> list[dict[str, object]]:
        with self.store.sessions() as session:
            return [
                {
                    "id": row.external_id,
                    "kind": row.kind,
                    "key": row.conversation_key,
                    "profile": self.store.cipher.decrypt_payload(
                        row.encrypted_profile, key_id=row.key_id
                    ),
                }
                for row in session.scalars(
                    select(GatewayConversation).where(GatewayConversation.channel_id == channel_id)
                )
            ]

    def members(self, channel_id: UUID, key: str) -> builtins.list[dict[str, object]]:
        from opsmesh.gateway.storage.models import GatewayIdentity

        with self.store.sessions() as session:
            return [
                {
                    "identity_id": str(row.identity_id),
                    "external_role": row.external_role,
                    "source": row.source,
                    "observed_at": row.updated_at.isoformat(),
                }
                for row in session.scalars(
                    select(GatewayMember)
                    .join(GatewayIdentity, GatewayMember.identity_id == GatewayIdentity.id)
                    .where(
                        GatewayIdentity.channel_id == channel_id,
                        GatewayMember.conversation_key == key,
                    )
                )
            ]
