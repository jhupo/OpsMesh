"""External identities and optional verified account associations are sender facts."""

from uuid import UUID

from opsmesh_sdk.channels import ExternalActor
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from opsmesh.gateway.storage.models import (
    GatewayAccountBinding,
    GatewayIdentity,
)
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.shared.serialization import payload_digest


class Identities:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store

    def observe(self, session: Session, channel_id: UUID, actor: ExternalActor) -> GatewayIdentity:
        key = payload_digest({"scope": actor.scope, "kind": actor.id_kind, "id": actor.external_id})
        row = session.scalar(
            select(GatewayIdentity).where(
                GatewayIdentity.channel_id == channel_id, GatewayIdentity.identity_key == key
            )
        )
        encrypted = self.store.cipher.encrypt_payload(actor.model_dump(mode="json"))
        if row is None:
            row = GatewayIdentity(
                channel_id=channel_id,
                identity_key=key,
                scope=actor.scope,
                id_kind=actor.id_kind,
                external_id=actor.external_id,
            )
            session.add(row)
        row.encrypted_profile, row.key_id = encrypted.ciphertext, encrypted.key_id
        session.flush()
        return row

    def bind(self, channel_id: UUID, identity_id: UUID, user_id: UUID, verified_by: UUID) -> None:
        with self.store.sessions.begin() as session:
            identity = session.get(GatewayIdentity, identity_id, with_for_update=True)
            if identity is None or identity.channel_id != channel_id:
                raise NotFoundError("Observed external identity does not exist")
            row = session.get(GatewayAccountBinding, identity_id)
            if row is not None and row.user_id != user_id:
                raise ConflictError("Unbind the previous account explicitly before rebinding")
            if row is None:
                session.add(
                    GatewayAccountBinding(
                        identity_id=identity_id, user_id=user_id, verified_by=verified_by
                    )
                )
            self.store.audit(session, "identity.bind", verified_by, identity_id)

    def unbind(self, identity_id: UUID, actor_id: UUID) -> None:
        with self.store.sessions.begin() as session:
            if session.get(GatewayIdentity, identity_id, with_for_update=True) is None:
                raise NotFoundError("External identity does not exist")
            session.execute(
                delete(GatewayAccountBinding).where(
                    GatewayAccountBinding.identity_id == identity_id
                )
            )
            self.store.audit(session, "identity.unbind", actor_id, identity_id)

    def list(self, channel_id: UUID) -> list[dict[str, object]]:
        with self.store.sessions() as session:
            result = []
            for row in session.scalars(
                select(GatewayIdentity).where(GatewayIdentity.channel_id == channel_id)
            ):
                binding = session.get(GatewayAccountBinding, row.id)
                profile = self.store.cipher.decrypt_payload(
                    row.encrypted_profile, key_id=row.key_id
                )
                result.append(
                    {
                        "id": str(row.id),
                        "scope": row.scope,
                        "id_kind": row.id_kind,
                        "external_id": row.external_id,
                        "name": profile.get("name", ""),
                        "user_id": str(binding.user_id) if binding else None,
                    }
                )
            return result
