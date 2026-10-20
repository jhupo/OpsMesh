"""Reauthorize each atomic owner batch and checkpoint in the same transaction."""

import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity

from opsmesh.identity.access import require_platform_admin
from opsmesh.identity.users.service import load_identity
from opsmesh.platform.operations.handlers import OperationHandlers, resolve
from opsmesh.platform.operations.models import PlatformOperation
from opsmesh.platform.operations.service import TERMINAL, record_event
from opsmesh.shared.errors import DomainError, ForbiddenError


class PlatformOperationActivities:
    def __init__(self, sessions: sessionmaker[Session], handlers: OperationHandlers) -> None:
        self.sessions = sessions
        self.handlers = handlers

    @activity.defn(name="platform.operation_page")
    async def execute_page(self, operation_id: str, generation: int = 1) -> str:
        return await asyncio.to_thread(self.execute_transaction, UUID(operation_id), generation)

    def execute_transaction(self, operation_id: UUID, generation: int | None = None) -> str:
        with self.sessions() as session, session.begin():
            operation = session.scalar(
                select(PlatformOperation)
                .where(PlatformOperation.id == operation_id)
                .with_for_update()
            )
            if operation is None:
                return "cancelled"
            if generation is not None and operation.generation != generation:
                return "cancelled"
            if operation.status in TERMINAL:
                return operation.status
            try:
                actor = load_identity(session, operation.requested_by_user_id)
                if actor is None:
                    raise ForbiddenError("The requesting administrator no longer exists")
                require_platform_admin(actor)
                handler = resolve(self.handlers, owner=operation.owner, command=operation.command)
                cursor = operation.result.get("cursor")
                with session.begin_nested():
                    page = handler(
                        session,
                        operation.target,
                        str(cursor) if cursor else None,
                        100,
                        actor,
                        operation.id,
                    )
                    if page.processed < 0 or (
                        page.next_cursor is not None and page.next_cursor == cursor
                    ):
                        raise ValueError("The owner returned an invalid batch cursor")
                    operation.result = {
                        "cursor": page.next_cursor,
                        "processed": (
                            int(str(operation.result.get("processed", 0))) + page.processed
                        ),
                        "details": page.details or {},
                    }
                    operation.status = "running" if page.next_cursor is not None else "completed"
                    if operation.status == "completed":
                        record_event(session, actor=actor, operation=operation, action="completed")
            except (DomainError, ValueError) as exc:
                operation.status = "needs_attention"
                operation.failure_code = (
                    exc.code if isinstance(exc, DomainError) else "invalid_batch"
                )
                operation.error_detail = "The owner batch was rejected; inspect its failure code"
            return operation.status

    @activity.defn(name="platform.operation_failed")
    async def failed(self, operation_id: str, generation: int) -> str:
        def checkpoint() -> str:
            with self.sessions() as session, session.begin():
                operation = session.get(PlatformOperation, UUID(operation_id), with_for_update=True)
                if operation is None or operation.generation != generation:
                    return "cancelled"
                if operation.status not in TERMINAL:
                    operation.status = "needs_attention"
                    operation.failure_code = "batch_execution_failed"
                    operation.error_detail = (
                        "The owner batch exhausted retries; resume after inspection"
                    )
                return operation.status

        return await asyncio.to_thread(checkpoint)
