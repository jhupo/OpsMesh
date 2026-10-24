"""Only a lease-holding Gateway Worker executes platform operations."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from opsmesh_sdk.channel_management import (
    ManagementRejected,
    ManagementResult,
    MemberQuery,
    MuteMember,
)
from opsmesh_sdk.channels import ChannelAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.gateway.channels.leases import require_lease
from opsmesh.gateway.contracts import ChannelDefinition
from opsmesh.gateway.management.authorization import authorize_operation
from opsmesh.gateway.management.contracts import OperationClaim, OperationOrigin
from opsmesh.gateway.management.service import Management
from opsmesh.gateway.storage.models import GatewayChannel, GatewayManagementOperation
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.shared.errors import DomainError, ForbiddenError

OriginAuthorizer = Callable[[OperationOrigin, ChannelDefinition], Awaitable[None]]
GenerationResolver = Callable[[str], str | None]


class ManagementWorker:
    def __init__(
        self,
        store: GatewayStore,
        authorize: OriginAuthorizer,
        generation: GenerationResolver,
        adapter_generation: Callable[[str], str],
    ) -> None:
        self.store, self.authorize, self.generation = store, authorize, generation
        self.service = Management(store, adapter_generation)

    def _record_state(
        self, session: Session, row: GatewayManagementOperation, status: str, code: str
    ) -> None:
        row.status, row.error_code = status, code
        self.store.audit(session, "management." + status, row.actor_id, row.id)

    def recover(self, channel_id: UUID, owner: str) -> None:
        with self.store.sessions.begin() as session:
            require_lease(session, channel_id, owner)
            for row in session.scalars(
                select(GatewayManagementOperation)
                .where(
                    GatewayManagementOperation.channel_id == channel_id,
                    GatewayManagementOperation.status == "executing",
                )
                .with_for_update()
            ):
                self._record_state(session, row, "uncertain", "worker_interrupted")

    def claim(self, channel_id: UUID, owner: str, adapter: ChannelAdapter) -> OperationClaim | None:
        with self.store.sessions.begin() as session:
            require_lease(session, channel_id, owner)
            channel = session.get(GatewayChannel, channel_id, with_for_update=True)
            if channel is None or channel.deleted or not channel.enabled:
                return None
            definition = self.store.definition(channel)
            now = datetime.now(UTC)
            rows = session.scalars(
                select(GatewayManagementOperation)
                .where(
                    GatewayManagementOperation.channel_id == channel_id,
                    GatewayManagementOperation.status.in_(("pending", "awaiting_approval")),
                )
                .order_by(GatewayManagementOperation.created_at, GatewayManagementOperation.id)
                .limit(100)
                .with_for_update()
            )
            for row in rows:
                if row.expires_at <= now:
                    self._record_state(session, row, "expired", "operation_expired")
                    continue
                if (row.channel_revision, row.binding_revision) != (
                    channel.revision,
                    channel.binding_revision,
                ):
                    self._record_state(session, row, "cancelled", "configuration_changed")
                    continue
                if row.resource_id and self.generation(row.resource_id) != row.generation:
                    self._record_state(session, row, "cancelled", "plugin_changed")
                    continue
                if row.adapter_generation != self.service.adapter_generation(definition.kind):
                    self._record_state(session, row, "cancelled", "adapter_changed")
                    continue
                if row.status == "awaiting_approval" or (row.retry_at and row.retry_at > now):
                    continue
                request = self.service.request(row)
                if isinstance(request, MuteMember) and request.expires_at <= now:
                    self._record_state(session, row, "expired", "mute_expired")
                    continue
                origin = OperationOrigin.model_validate(
                    {
                        "kind": row.source_kind,
                        "actor_id": row.actor_id,
                        "resource_id": row.resource_id,
                        "generation": row.generation,
                        "source_id": row.source_id,
                    }
                )
                try:
                    requester_id, roles, message_at = authorize_operation(
                        session, self.store, definition, origin, request
                    )
                except DomainError:
                    self._record_state(session, row, "cancelled", "authorization_revoked")
                    continue
                if (
                    request.action not in adapter.capabilities.management
                    or adapter.management is None
                ):
                    self._record_state(session, row, "failed", "unsupported")
                    continue
                capability = next(
                    (
                        item
                        for item in adapter.management.capabilities
                        if item.action == request.action
                    ),
                    None,
                )
                if (
                    message_at is not None
                    and capability
                    and capability.max_message_age_seconds is not None
                    and not 0
                    <= (now - message_at).total_seconds()
                    < capability.max_message_age_seconds
                ):
                    self._record_state(session, row, "expired", "message_expired")
                    continue
                row.status, row.attempts = "executing", row.attempts + 1
                row.error_code = None
                return OperationClaim(row.id, request, origin, requester_id, roles, row.approved_by)
        return None

    def finish(
        self,
        channel_id: UUID,
        owner: str,
        operation_id: UUID,
        status: str,
        *,
        result: ManagementResult | None = None,
        code: str | None = None,
        retryable: bool = False,
    ) -> None:
        with self.store.sessions.begin() as session:
            require_lease(session, channel_id, owner)
            row = session.get(GatewayManagementOperation, operation_id, with_for_update=True)
            if row is None or row.channel_id != channel_id or row.status != "executing":
                return
            if retryable and row.attempts < 3:
                row.status = "pending"
                row.retry_at = datetime.now(UTC) + timedelta(seconds=10 * row.attempts)
            else:
                row.status = status
            row.error_code = code
            if result is not None:
                row.encrypted_result = self.store.cipher.encrypt_payload(
                    result.model_dump(mode="json")
                ).ciphertext
            self.store.audit(session, "management." + row.status, row.actor_id, row.id)

    async def run(self, definition: ChannelDefinition, owner: str, adapter: ChannelAdapter) -> None:
        await asyncio.to_thread(self.recover, definition.id, owner)
        while True:
            claim = await asyncio.to_thread(self.claim, definition.id, owner, adapter)
            if claim is None:
                await asyncio.sleep(1)
                continue
            try:
                await self.authorize(claim.origin, definition)
                if claim.approver_id is not None:
                    await self.authorize(
                        OperationOrigin(kind="admin", actor_id=claim.approver_id), definition
                    )
                assert adapter.management is not None
                async with asyncio.timeout(30):
                    if claim.requester_id is not None:
                        member = await adapter.management.execute(
                            MemberQuery(
                                conversation=claim.request.conversation,
                                member_id=claim.requester_id,
                            )
                        )
                        if member.data.get("role") not in claim.requester_roles:
                            raise ForbiddenError("The requester's group role was revoked")
                    # Recheck the lease, configuration and grant after platform reads.
                    await asyncio.to_thread(self.revalidate, definition.id, owner, claim)
                    result = await adapter.management.execute(claim.request)
            except asyncio.CancelledError:
                await asyncio.shield(
                    asyncio.to_thread(
                        self.finish,
                        definition.id,
                        owner,
                        claim.id,
                        "uncertain",
                        code="worker_interrupted",
                    )
                )
                raise
            except DomainError:
                await asyncio.to_thread(
                    self.finish,
                    definition.id,
                    owner,
                    claim.id,
                    "cancelled",
                    code="authorization_revoked",
                )
            except ManagementRejected as error:
                await asyncio.to_thread(
                    self.finish,
                    definition.id,
                    owner,
                    claim.id,
                    "expired"
                    if error.code in {"operation_expired", "message_expired", "mute_expired"}
                    else "failed",
                    code=error.code,
                    retryable=error.retryable,
                )
            except Exception:
                await asyncio.to_thread(
                    self.finish,
                    definition.id,
                    owner,
                    claim.id,
                    "uncertain",
                    code="platform_outcome_unknown",
                )
            else:
                await asyncio.to_thread(
                    self.finish, definition.id, owner, claim.id, "succeeded", result=result
                )
            await asyncio.sleep(2)

    def revalidate(self, channel_id: UUID, owner: str, claim: OperationClaim) -> None:
        with self.store.sessions.begin() as session:
            require_lease(session, channel_id, owner)
            channel = session.get(GatewayChannel, channel_id, with_for_update=True)
            row = session.get(GatewayManagementOperation, claim.id)
            if (
                channel is None
                or row is None
                or row.channel_revision != channel.revision
                or row.binding_revision != channel.binding_revision
                or row.status != "executing"
            ):
                raise ForbiddenError("The management claim changed")
            if (
                claim.origin.resource_id
                and self.generation(claim.origin.resource_id) != claim.origin.generation
            ):
                raise ForbiddenError("The plugin changed")
            if row.adapter_generation != self.service.adapter_generation(channel.kind):
                raise ForbiddenError("The channel adapter changed")
            if row.expires_at <= datetime.now(UTC):
                raise ManagementRejected("operation_expired")
            authorize_operation(
                session, self.store, self.store.definition(channel), claim.origin, claim.request
            )
