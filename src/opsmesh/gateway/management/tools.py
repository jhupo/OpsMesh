"""Host capability scoped to a real tool invocation and its gateway receipt."""

import asyncio
from collections.abc import Callable
from uuid import UUID

from opsmesh_sdk.channel_management import ManagementRequest, OperationReceipt
from opsmesh_sdk.identity import InvocationSource

from opsmesh.gateway.management.contracts import OperationOrigin
from opsmesh.gateway.management.service import Management
from opsmesh.shared.errors import ForbiddenError


class BoundManagement:
    def __init__(
        self,
        service: Management,
        source: InvocationSource,
        origin: OperationOrigin,
        operation_id: UUID,
        authorize: Callable[[], None],
    ) -> None:
        self.service, self.source, self.origin, self.operation_id, self.authorize = (
            service,
            source,
            origin,
            operation_id,
            authorize,
        )

    async def submit(self, request: ManagementRequest) -> OperationReceipt:
        def submit() -> OperationReceipt:
            self.authorize()
            if self.source.channel_id is None or self.source.receipt_id is None:
                raise ForbiddenError("Management tools require a gateway message receipt")
            if (
                request.conversation.scope != self.source.identity_scope
                or request.conversation.external_id != self.source.conversation_id
            ):
                raise ForbiddenError("The tool can manage only its source conversation")
            return self.service.submit(
                self.source.channel_id, self.operation_id, self.origin, request
            )

        return await asyncio.to_thread(submit)

    async def get(self, operation_id: UUID) -> OperationReceipt:
        def get() -> OperationReceipt:
            self.authorize()
            if self.source.channel_id is None:
                raise ForbiddenError("Management tools require a gateway source")
            return self.service.get(self.source.channel_id, operation_id, origin=self.origin)

        return await asyncio.to_thread(get)
