"""Compose transport ports with authorized execution and workflow services."""

import asyncio
import base64
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from opsmesh_sdk.channels import ContentPart
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.gateway.contracts import GatewayEvent, GatewayEvents, GatewayTurn, RouteDefinition
from opsmesh.gateway.messaging.media import GatewayMedia
from opsmesh.gateway.routing.service import authorize_turn
from opsmesh.gateway.routing.sessions import mapped_session
from opsmesh.gateway.storage.models import GatewayInbox
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import canonical_json


class GatewayExecution:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        cipher: SecretCipher,
        history_root: Path,
        media_root: Path,
    ) -> None:
        self.sessions, self.cipher = sessions, cipher
        self.history_root, self.media_root = history_root, media_root

    def check(self, session: Session, user_id: UUID, route: RouteDefinition) -> Any:
        from opsmesh.execution.instances.access import get_instance
        from opsmesh.users.service import current_user
        from opsmesh.workflows.instances.access import get_instance as get_workflow

        actor = current_user(session, user_id)
        if route.target_kind == "agent":
            get_instance(session, actor=actor, instance_id=route.target_id)
        else:
            get_workflow(session, actor=actor, instance_id=route.target_id)
        return actor

    async def authorize(self, user_id: UUID, route: RouteDefinition) -> None:
        def check() -> None:
            with self.sessions() as session:
                self.check(session, user_id, route)

        await asyncio.to_thread(check)

    async def submit(self, turn: GatewayTurn) -> UUID:
        media = GatewayMedia(self.media_root / str(turn.channel_id))

        async def native_content(parts: list[ContentPart]) -> list[dict[str, Any]]:
            content: list[dict[str, Any]] = []
            for part in await media.prepare(parts):
                if part.type == "text":
                    content.append({"type": "input_text", "text": part.text})
                elif part.media_key:
                    data = base64.b64encode(await media.read(part.media_key)).decode()
                    url = f"data:{part.mime_type};base64,{data}"
                    content.append(
                        {"type": "input_image", "image_url": url}
                        if part.type == "image"
                        else {"type": "input_file", "filename": part.filename, "file_data": url}
                    )
            return content

        content = await native_content(turn.message.content)
        history: list[dict[str, Any]] = []
        seen = {turn.message.event_id}
        for identifier, sender, parts in [
            (item.event_id, item.actor.external_id, item.content) for item in turn.recent_messages
        ] + [
            (item.message_id, item.sender_id, item.content)
            for item in turn.message.context.recent_messages
        ]:
            if identifier in seen:
                continue
            seen.add(identifier)
            history.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Untrusted recent group message: "
                            + canonical_json({"message_id": identifier, "sender_id": sender}),
                        },
                        *await native_content(parts),
                    ],
                }
            )

        def submit() -> UUID:
            from opsmesh.execution.runs.schemas import RunCreate
            from opsmesh.execution.runs.service import submit_run
            from opsmesh.execution.sessions.service import create_session
            from opsmesh.workflows.engines.temporal.engine import TemporalWorkflowEngine
            from opsmesh.workflows.engines.temporal.execution_dispatch import dispatch_run
            from opsmesh.workflows.runs.schemas import RunCreate as WorkflowCreate
            from opsmesh.workflows.runs.service import submit_run as submit_workflow

            with self.sessions.begin() as session:
                authorize_turn(session, turn)
                actor = self.check(session, turn.user_id, turn.route)
                row = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
                assert row is not None
                if row.run_id is not None:
                    return row.run_id
                request_key = "gateway:" + str(turn.inbox_id)
                facts = {
                    "user_id": str(actor.user_id),
                    "platform_roles": sorted(actor.platform_roles),
                    "external": turn.message.actor.model_dump(mode="json"),
                    "conversation": turn.message.conversation.model_dump(mode="json"),
                    "message_context": turn.message.context.model_dump(mode="json"),
                    "recent_messages": [
                        {
                            "event_id": item.event_id,
                            "sender_id": item.actor.external_id,
                            "content": [part.model_dump(mode="json") for part in item.content],
                        }
                        for item in turn.recent_messages
                    ]
                    + [
                        item.model_dump(mode="json")
                        for item in turn.message.context.recent_messages
                    ],
                }
                messages = [*history, {"role": "user", "content": content}]
                if turn.route.target_kind == "agent":
                    session_id = mapped_session(
                        session,
                        turn,
                        lambda: (
                            create_session(
                                session, actor=actor, instance_id=turn.route.target_id
                            ).id
                        ),
                    )
                    run = submit_run(
                        session,
                        actor=actor,
                        cipher=self.cipher,
                        source=turn.source,
                        request=RunCreate(
                            instance_id=turn.route.target_id,
                            session_id=session_id,
                            driver="temporal",
                            request_key=request_key,
                            input=messages,
                            timeout_seconds=turn.policy.response_timeout_seconds,
                        ),
                    )
                    dispatch_run(session, run.id)
                else:
                    workflow_run = submit_workflow(
                        session,
                        actor=actor,
                        cipher=self.cipher,
                        source=turn.source,
                        request=WorkflowCreate(
                            instance_id=turn.route.target_id,
                            request_key=request_key,
                            input={"messages": messages, "gateway": facts},
                        ),
                    )
                    TemporalWorkflowEngine(session).start(workflow_run.id)
                run_id = run.id if turn.route.target_kind == "agent" else workflow_run.id
                # Record dispatch and inbox association atomically with the execution outbox.
                row.run_id, row.route_id, row.user_id = run_id, turn.route.id, turn.user_id
                row.status, row.route_revision = "submitted", turn.route.revision
                return run_id

        return await asyncio.to_thread(submit)

    async def result(self, turn: GatewayTurn, run_id: UUID) -> tuple[str, object]:
        def read() -> tuple[str, object]:
            from opsmesh.execution.runs.access import get_run
            from opsmesh.workflows.runs.access import get_run as get_workflow_run

            with self.sessions.begin() as session:
                authorize_turn(session, turn)
                actor = self.check(session, turn.user_id, turn.route)
                row = (get_run if turn.route.target_kind == "agent" else get_workflow_run)(
                    session, actor=actor, run_id=run_id
                )
                output = (
                    self.cipher.decrypt_payload(row.encrypted_result).get("output")
                    if row.status == "completed" and row.encrypted_result
                    else None
                )
                return row.status, output

        return await asyncio.to_thread(read)

    async def events(self, turn: GatewayTurn, run_id: UUID, cursor: int) -> GatewayEvents:
        def read() -> GatewayEvents:
            from opsmesh.execution.runs.access import get_run
            from opsmesh.execution.runs.models import ExecutionEvent
            from opsmesh.workflows.runs.access import get_run as get_workflow_run
            from opsmesh.workflows.runs.models import WorkflowEvent

            with self.sessions.begin() as session:
                authorize_turn(session, turn)
                actor = self.check(session, turn.user_id, turn.route)
                agent = turn.route.target_kind == "agent"
                (get_run if agent else get_workflow_run)(session, actor=actor, run_id=run_id)
                rows: Sequence[ExecutionEvent | WorkflowEvent]
                if agent:
                    rows = session.scalars(
                        select(ExecutionEvent)
                        .where(ExecutionEvent.run_id == run_id, ExecutionEvent.sequence > cursor)
                        .order_by(ExecutionEvent.sequence)
                        .limit(100)
                    ).all()
                else:
                    rows = session.scalars(
                        select(WorkflowEvent)
                        .where(WorkflowEvent.run_id == run_id, WorkflowEvent.sequence > cursor)
                        .order_by(WorkflowEvent.sequence)
                        .limit(100)
                    ).all()
                events = []
                for row in rows:
                    payload = self.cipher.decrypt_payload(row.encrypted_payload, key_id=row.key_id)
                    if agent and row.kind == "model_text_delta":
                        events.append(
                            GatewayEvent(
                                sequence=row.sequence,
                                kind="delta",
                                text=str(payload.get("delta") or "")[:100_000],
                            )
                        )
                    elif agent and row.kind == "model_summary_delta":
                        events.append(
                            GatewayEvent(
                                sequence=row.sequence,
                                kind="summary",
                                text=str(payload.get("delta") or "")[:100_000],
                            )
                        )
                    elif row.kind in {"operation_started", "node_running"} and (
                        not agent or payload.get("kind") in {"tool", "mcp"}
                    ):
                        events.append(
                            GatewayEvent(
                                sequence=row.sequence,
                                kind="tool_start",
                                text="Processing a task step.",
                            )
                        )
                    elif row.kind in {"operation_completed", "node_completed"} and (
                        not agent or payload.get("kind") in {"tool", "mcp"}
                    ):
                        events.append(
                            GatewayEvent(
                                sequence=row.sequence, kind="tool_end", text="Task step completed."
                            )
                        )
                return GatewayEvents(cursor=rows[-1].sequence if rows else cursor, events=events)

        return await asyncio.to_thread(read)

    async def control(self, turn: GatewayTurn, command: str) -> str:
        def apply() -> str:
            from opsmesh.execution.runs.models import ACTIVE, ExecutionRun
            from opsmesh.execution.runs.service import cancel_run
            from opsmesh.execution.sessions.service import create_session, reset_session
            from opsmesh.gateway.messaging.dispatcher import reply_parts
            from opsmesh.gateway.messaging.outbox import Outbox
            from opsmesh.gateway.storage.store import GatewayStore
            from opsmesh.workflows.engines.temporal.execution_dispatch import dispatch_cancellation

            with self.sessions.begin() as session:
                authorize_turn(session, turn)
                actor = self.check(session, turn.user_id, turn.route)
                inbox = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
                assert inbox is not None
                if inbox.status == "completed":
                    return "Command already processed."
                if turn.route.target_kind != "agent":
                    raise ConflictError("Conversation commands require an Agent route")

                def factory() -> UUID:
                    return create_session(session, actor=actor, instance_id=turn.route.target_id).id

                session_id = mapped_session(session, turn, factory, fresh=command == "new")
                if command == "cancel":
                    runs = list(
                        session.scalars(
                            select(ExecutionRun).where(
                                ExecutionRun.user_id == actor.user_id,
                                ExecutionRun.session_id == session_id,
                                ExecutionRun.status.in_(ACTIVE),
                            )
                        )
                    )
                    for run in runs:
                        cancel_run(session, actor=actor, run_id=run.id, cipher=self.cipher)
                        dispatch_cancellation(session, run)
                    result = f"Cancellation requested for {len(runs)} task(s)."
                elif command == "reset":
                    reset_session(
                        session, actor=actor, session_id=session_id, history_root=self.history_root
                    )
                    result = "Private conversation history reset."
                elif command == "new":
                    result = "Switched to a new private conversation."
                else:
                    raise ConflictError("Unknown gateway command")
                # Command effects and reply intent commit together, making reconnects idempotent.
                inbox.status, inbox.route_id, inbox.user_id = (
                    "completed",
                    turn.route.id,
                    turn.user_id,
                )
                inbox.route_revision = turn.route.revision
                Outbox(GatewayStore(self.sessions, self.cipher)).enqueue(
                    session, turn, reply_parts(result)
                )
                return result

        return await asyncio.to_thread(apply)
