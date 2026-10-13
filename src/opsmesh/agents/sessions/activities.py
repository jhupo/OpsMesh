"""Authorized native Session Activities with atomic mutation receipts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import cast

from agents.extensions.memory import SQLAlchemySession
from agents.items import TResponseInputItem
from sqlalchemy import Connection, event, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import Session
from temporalio import activity

from opsmesh.agents.execution.providers.openai.contracts import AgentSessionReference
from opsmesh.agents.sessions.contracts import SessionOperation
from opsmesh.agents.sessions.models import (
    ACTIVE_SESSION_STATUS,
    AgentSessionMetadata,
    SDKSessionMessageRecord,
    SDKSessionOperationReceipt,
)
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.statuses import ACTIVE_RUN_STATUS_VALUES


@dataclass
class _Mutation:
    operation: SessionOperation
    operation_id: str
    fingerprint: str
    result: object | None = None
    record_receipt: bool = True


class _RecordedMutation(Exception):
    """Exit an SDK mutation when its durable idempotency receipt already exists.

    This is a private control signal carrying the recorded result, not a failure
    reported to a product caller.
    """

    def __init__(self, result: object | None) -> None:
        self.result = result


_mutation: ContextVar[_Mutation | None] = ContextVar("sdk_session_mutation", default=None)


class SessionActivities:
    """SDK owns history SQL; public SQLAlchemy events add one atomic business receipt."""

    def __init__(self, engine: AsyncEngine, session_factory: Callable[[], Session]) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("Durable SDK Session Activities require PostgreSQL")
        self._engine = engine
        self._session_factory = session_factory
        event.listen(engine.sync_engine, "begin", self._before_transaction)
        event.listen(engine.sync_engine, "commit", self._before_commit)

    async def close(self) -> None:
        event.remove(self._engine.sync_engine, "begin", self._before_transaction)
        event.remove(self._engine.sync_engine, "commit", self._before_commit)

    def _authorize(self, reference: AgentSessionReference) -> None:
        with self._session_factory() as session:
            ExecutionIdentityService(session).for_run(reference.workspace_id, reference.run_id)
            self._assert_scope(session, reference)

    @staticmethod
    def _assert_scope(session: Session, reference: AgentSessionReference) -> None:
        run = session.scalar(
            select(AgentRun.id).where(
                AgentRun.workspace_id == reference.workspace_id,
                AgentRun.id == reference.run_id,
                AgentRun.session_key == reference.session_id,
                AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
            )
        )
        metadata = session.scalar(
            select(AgentSessionMetadata.id).where(
                AgentSessionMetadata.workspace_id == reference.workspace_id,
                AgentSessionMetadata.session_key == reference.session_id,
                AgentSessionMetadata.status == ACTIVE_SESSION_STATUS,
            )
        )
        if run is None or metadata is None:
            raise ResourceAccessDeniedError()

    @staticmethod
    def _before_transaction(connection: Connection) -> None:
        mutation = _mutation.get()
        if mutation is None:
            return
        reference = mutation.operation.reference
        run = connection.execute(
            select(AgentRun.id)
            .where(
                AgentRun.workspace_id == reference.workspace_id,
                AgentRun.id == reference.run_id,
                AgentRun.session_key == reference.session_id,
                AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
            )
            .with_for_update()
        ).scalar_one_or_none()
        if run is None:
            raise ResourceAccessDeniedError()
        metadata = connection.execute(
            select(AgentSessionMetadata.id)
            .where(
                AgentSessionMetadata.workspace_id == reference.workspace_id,
                AgentSessionMetadata.session_key == reference.session_id,
                AgentSessionMetadata.status == ACTIVE_SESSION_STATUS,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if metadata is None:
            raise ResourceAccessDeniedError()
        receipt = (
            connection.execute(
                select(
                    SDKSessionOperationReceipt.method,
                    SDKSessionOperationReceipt.fingerprint,
                    SDKSessionOperationReceipt.session_id,
                    SDKSessionOperationReceipt.run_id,
                    SDKSessionOperationReceipt.result,
                ).where(
                    SDKSessionOperationReceipt.workspace_id == reference.workspace_id,
                    SDKSessionOperationReceipt.operation_id == mutation.operation_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if receipt is not None:
            if (
                receipt["method"] != mutation.operation.method
                or receipt["fingerprint"] != mutation.fingerprint
                or receipt["session_id"] != reference.session_id
                or receipt["run_id"] != reference.run_id
            ):
                raise ValueError("Session mutation identity was reused with different input")
            raise _RecordedMutation(receipt["result"])
        if mutation.operation.method == "pop_item":
            mutation.result = None
            mutation.record_receipt = True
            tail = connection.execute(
                select(SDKSessionMessageRecord.message_data)
                .where(SDKSessionMessageRecord.session_id == reference.session_id)
                .order_by(
                    SDKSessionMessageRecord.created_at.desc(), SDKSessionMessageRecord.id.desc()
                )
                .limit(1)
            ).scalar_one_or_none()
            if tail is not None:
                try:
                    mutation.result = json.loads(tail)
                except (json.JSONDecodeError, TypeError):
                    # SDK may discard malformed rows before returning an item.
                    # That cleanup transaction is not the completed operation.
                    mutation.record_receipt = False

    @staticmethod
    def _before_commit(connection: Connection) -> None:
        mutation = _mutation.get()
        if mutation is None or not mutation.record_receipt:
            return
        reference = mutation.operation.reference
        connection.execute(
            insert(SDKSessionOperationReceipt).values(
                workspace_id=reference.workspace_id,
                run_id=reference.run_id,
                session_id=reference.session_id,
                operation_id=mutation.operation_id,
                method=mutation.operation.method,
                fingerprint=mutation.fingerprint,
                result=mutation.result,
            )
        )

    @activity.defn(name="opsmesh.sdk_session")
    async def operate(self, operation: SessionOperation) -> object:
        info = activity.info()
        return await self.execute(operation, operation_id=f"{info.workflow_id}:{info.activity_id}")

    async def execute(self, operation: SessionOperation, *, operation_id: str) -> object:
        """Apply a native Session operation with its explicit durable caller identity."""
        await asyncio.to_thread(self._authorize, operation.reference)
        native = SQLAlchemySession(
            operation.reference.session_id,
            engine=self._engine,
            create_tables=False,
            sessions_table="sdk_agent_sessions",
            messages_table="sdk_agent_messages",
        )
        if operation.method == "get_items":
            return await native.get_items(operation.limit)
        fingerprint = hashlib.sha256(operation.model_dump_json().encode()).hexdigest()
        mutation = _Mutation(operation, operation_id, fingerprint)
        token = _mutation.set(mutation)
        try:
            if operation.method == "add_items":
                await native.add_items(cast(list[TResponseInputItem], operation.items))
                return None
            if operation.method == "pop_item":
                return await native.pop_item()
            await native.clear_session()
            return None
        except _RecordedMutation as recorded:
            return cast(TResponseInputItem | None, recorded.result)
        finally:
            _mutation.reset(token)
