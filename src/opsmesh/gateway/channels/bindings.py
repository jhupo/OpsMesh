"""Owned, expiring QR tasks create disabled channels without binding users."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from cryptography.exceptions import InvalidTag
from sqlalchemy import select

from opsmesh.gateway.adapters.qq import binding
from opsmesh.gateway.adapters.qq.config import QQConfig
from opsmesh.gateway.contracts import ChannelDefinition
from opsmesh.gateway.storage.models import GatewayBinding
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.shared.errors import ConflictError, NotFoundError


class Bindings:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store

    def expire(self) -> None:
        with self.store.sessions.begin() as session:
            for row in session.scalars(
                select(GatewayBinding)
                .where(
                    GatewayBinding.status == "pending",
                    GatewayBinding.expires_at <= datetime.now(UTC),
                )
                .with_for_update(skip_locked=True)
                .limit(100)
            ):
                row.status, row.encrypted_state = "expired", None
                self.store.audit(session, "binding.expired", row.owner_id, row.id)

    def create(self, owner_id: UUID, name: str) -> dict[str, Any]:
        try:
            state = binding.create()
        except (httpx.HTTPError, ValueError) as error:
            raise ConflictError("QQ binding service is unavailable") from error
        expires = datetime.now(UTC) + timedelta(minutes=8)
        encrypted = self.store.cipher.encrypt_payload(dict(state))
        with self.store.sessions.begin() as session:
            row = GatewayBinding(
                id=uuid4(),
                owner_id=owner_id,
                name=name,
                expires_at=expires,
                encrypted_state=encrypted.ciphertext,
                key_id=encrypted.key_id,
            )
            session.add(row)
            self.store.audit(session, "binding.create", owner_id, row.id)
            return {
                "id": str(row.id),
                "status": "pending",
                "expires_at": expires,
                "url": state["url"],
            }

    def poll(self, identifier: UUID, owner_id: UUID, *, cancel: bool = False) -> dict[str, Any]:
        with self.store.sessions.begin() as session:
            row = session.get(GatewayBinding, identifier, with_for_update=True)
            if row is None or row.owner_id != owner_id:
                raise NotFoundError("Binding task does not exist")
            if row.status == "pending":
                if cancel or row.expires_at <= datetime.now(UTC):
                    row.status = "cancelled" if cancel else "expired"
                else:
                    assert row.encrypted_state is not None
                    state = self.store.cipher.decrypt_payload(
                        row.encrypted_state, key_id=row.key_id
                    )
                    try:
                        row.status, config = binding.poll(state)
                        if row.status == "completed":
                            config = QQConfig.model_validate(config).model_dump()
                    except (httpx.HTTPError, ValueError, KeyError, InvalidTag) as error:
                        raise ConflictError("QQ binding poll failed") from error
                    if row.status == "completed":
                        definition = ChannelDefinition(
                            id=uuid4(), owner_id=owner_id, kind="qq", name=row.name, config=config
                        )
                        self.store.save_in(session, definition, owner_id)
                        row.channel_id = definition.id
                if row.status != "pending":
                    row.encrypted_state = None
                    self.store.audit(session, "binding." + row.status, owner_id, row.id)
            return {
                "id": str(row.id),
                "status": row.status,
                "expires_at": row.expires_at,
                "channel_id": str(row.channel_id) if row.channel_id else None,
            }
