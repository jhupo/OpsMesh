"""Compose transport ports with authorized execution and workflow services."""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from opsmesh_sdk.messages import MessageFilterResult
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.gateway.contracts import (
    ChannelDefinition,
    ChannelTarget,
    GatewayEvent,
    GatewayEvents,
    GatewayTurn,
)
from opsmesh.gateway.management.contracts import OperationOrigin
from opsmesh.gateway.messaging.inputs import native_messages
from opsmesh.gateway.messaging.media import GatewayMedia
from opsmesh.gateway.routing.service import authorize_turn
from opsmesh.gateway.routing.sessions import mapped_session
from opsmesh.gateway.storage.models import GatewayChannel, GatewayInbox
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError
from opsmesh.shared.security.encryption import SecretCipher


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

    def check(self, session: Session, user_id: UUID, target: ChannelTarget) -> Any:
        from opsmesh.execution.instances.access import get_instance
        from opsmesh.users.service import current_user
        from opsmesh.workflows.instances.access import get_instance as get_workflow

        actor = current_user(session, user_id)
        if target.target_kind == "agent":
            get_instance(session, actor=actor, instance_id=target.target_id)
        else:
            get_workflow(session, actor=actor, instance_id=target.target_id)
        return actor

    async def authorize(self, user_id: UUID, target: ChannelTarget) -> None:
        def check() -> None:
            with self.sessions() as session:
                self.check(session, user_id, target)

        await asyncio.to_thread(check)

    async def filter_actor(self, turn: GatewayTurn) -> Any:
        def current() -> Any:
            from opsmesh.users.service import current_user

            with self.sessions() as session:
                authorize_turn(session, GatewayStore(self.sessions, self.cipher), turn)
                return current_user(session, turn.user_id)

        return await asyncio.to_thread(current)

    async def management_actor(self, origin: OperationOrigin, channel: ChannelDefinition) -> None:
        def check() -> None:
            from opsmesh.users.access import require_platform_admin
            from opsmesh.users.service import current_user

            with self.sessions() as session:
                actor = current_user(session, origin.actor_id)
                if channel.target is None:
                    raise ForbiddenError("The channel has no target")
                if origin.kind == "admin":
                    require_platform_admin(actor)
                elif actor.user_id != channel.target.user_id:
                    raise ForbiddenError("The operation no longer belongs to the executor")
                self.check(session, channel.target.user_id, channel.target)

        await asyncio.to_thread(check)

    async def submit(self, turn: GatewayTurn) -> UUID:
        media = GatewayMedia(self.media_root / str(turn.channel_id))

        messages = await native_messages(turn, media)

        def submit() -> UUID:
            from opsmesh.execution.runs.schemas import RunCreate
            from opsmesh.execution.runs.service import submit_run
            from opsmesh.execution.sessions.service import create_session
            from opsmesh.workflows.engines.temporal.engine import TemporalWorkflowEngine
            from opsmesh.workflows.engines.temporal.execution_dispatch import dispatch_run
            from opsmesh.workflows.runs.schemas import RunCreate as WorkflowCreate
            from opsmesh.workflows.runs.service import submit_run as submit_workflow

            with self.sessions.begin() as session:
                authorize_turn(session, GatewayStore(self.sessions, self.cipher), turn)
                actor = self.check(session, turn.user_id, turn.target)
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": "gateway-queue:" + str(turn.source.session_scope)},
                )
                row = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
                assert row is not None
                if row.run_id is not None:
                    return row.run_id
                if row.status != "dispatching":
                    raise ConflictError("Only an admitted conversation batch can be submitted")
                channel = session.get(GatewayChannel, turn.channel_id)
                if channel is None or row.filter_revision != channel.revision:
                    raise ConflictError("Channel configuration changed before submission")
                request_key = "gateway:" + str(turn.inbox_id)
                facts = {
                    "user_id": str(actor.user_id),
                    "platform_roles": sorted(actor.platform_roles),
                    "external": turn.message.actor.model_dump(mode="json"),
                    "conversation": turn.message.conversation.model_dump(mode="json"),
                    "message_context": turn.message.context.model_dump(
                        mode="json", exclude={"recent_messages"}
                    ),
                    "batch": [
                        item.message.model_dump(
                            mode="json", exclude={"context": {"recent_messages"}}
                        )
                        for item in turn.batch
                    ],
                }
                if turn.target.target_kind == "agent":
                    session_id = mapped_session(
                        session,
                        turn,
                        lambda: (
                            create_session(
                                session, actor=actor, instance_id=turn.target.target_id
                            ).id
                        ),
                    )
                    run = submit_run(
                        session,
                        actor=actor,
                        cipher=self.cipher,
                        source=turn.source,
                        request=RunCreate(
                            instance_id=turn.target.target_id,
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
                            instance_id=turn.target.target_id,
                            request_key=request_key,
                            input={"messages": messages, "gateway": facts},
                        ),
                    )
                    TemporalWorkflowEngine(session).start(workflow_run.id)
                run_id = run.id if turn.target.target_kind == "agent" else workflow_run.id
                # Record dispatch and inbox association atomically with the execution outbox.
                row.run_id, row.target_id, row.user_id = run_id, turn.target.target_id, turn.user_id
                row.target_kind = turn.target.target_kind
                row.status, row.binding_revision = "submitted", turn.binding_revision
                return run_id

        return await asyncio.to_thread(submit)

    async def submit_rejection(self, turn: GatewayTurn) -> UUID:
        def submit() -> UUID:
            from opsmesh.agents.config.callbacks import CallbackRef
            from opsmesh.execution.instances.access import get_instance
            from opsmesh.execution.preparation import AgentSnapshot
            from opsmesh.execution.runs.schemas import RunCreate
            from opsmesh.execution.runs.service import submit_run
            from opsmesh.workflows.engines.temporal.execution_dispatch import dispatch_run

            with self.sessions.begin() as session:
                authorize_turn(session, GatewayStore(self.sessions, self.cipher), turn)
                actor = self.check(session, turn.user_id, turn.target)
                row = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
                channel = session.get(GatewayChannel, turn.channel_id)
                assert row is not None
                context = self.cipher.decrypt_payload(row.encrypted_context or "")
                if (
                    row.status != "responding"
                    or channel is None
                    or context.get("rejection_policy") != turn.policy.rejection_fingerprint
                ):
                    raise ConflictError("Rejection admission or channel configuration changed")
                if row.run_id is not None:
                    return row.run_id
                admission = context["admission"]
                decision = MessageFilterResult.model_validate(admission)
                if (
                    decision.action == "allow"
                    or not decision.rejection_input
                    or turn.policy.rejection_agent_id is None
                ):
                    raise InvalidArgumentError("The message has no rejection generation request")
                instance = get_instance(
                    session, actor=actor, instance_id=turn.policy.rejection_agent_id, lock=True
                )
                snapshot = AgentSnapshot.model_validate(
                    self.cipher.decrypt_payload(instance.encrypted_config)
                )
                if snapshot.requires_sandbox or any(
                    config.tools
                    or config.mcp_servers
                    or config.handoffs
                    or config.output_type is not None
                    or config.hooks
                    or config.input_guardrails
                    or config.output_guardrails
                    or not isinstance(config.instructions, (str, type(None)))
                    or config.prompt is not None
                    or isinstance(config.tool_use_behavior, CallbackRef)
                    or any(
                        (config.model_settings or {}).get(key)
                        for key in ("extra_args", "extra_body", "extra_query")
                    )
                    for config in snapshot.definitions.values()
                ):
                    raise InvalidArgumentError(
                        "Rejection Agents must be text-only without tools, callbacks "
                        "or remote prompts"
                    )
                run = submit_run(
                    session,
                    actor=actor,
                    cipher=self.cipher,
                    source=turn.source.model_copy(update={"stream_output": False}),
                    request=RunCreate(
                        instance_id=instance.id,
                        driver="temporal",
                        request_key="gateway-rejection:" + str(row.id),
                        input=decision.rejection_input,
                        max_turns=1,
                        timeout_seconds=turn.policy.rejection_timeout_seconds,
                    ),
                )
                dispatch_run(session, run.id)
                row.run_id = run.id
                return run.id

        return await asyncio.to_thread(submit)

    async def result(self, turn: GatewayTurn, run_id: UUID) -> tuple[str, object]:
        def read() -> tuple[str, object]:
            from opsmesh.execution.runs.access import get_run
            from opsmesh.workflows.runs.access import get_run as get_workflow_run

            with self.sessions.begin() as session:
                authorize_turn(session, GatewayStore(self.sessions, self.cipher), turn)
                actor = self.check(session, turn.user_id, turn.target)
                row = (
                    get_run
                    if turn.status == "responding" or turn.target.target_kind == "agent"
                    else get_workflow_run
                )(session, actor=actor, run_id=run_id)
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
                authorize_turn(session, GatewayStore(self.sessions, self.cipher), turn)
                actor = self.check(session, turn.user_id, turn.target)
                agent = turn.target.target_kind == "agent"
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
            from opsmesh.workflows.engines.temporal.execution_dispatch import dispatch_cancellation

            with self.sessions.begin() as session:
                authorize_turn(session, GatewayStore(self.sessions, self.cipher), turn)
                actor = self.check(session, turn.user_id, turn.target)
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": "gateway-queue:" + str(turn.source.session_scope)},
                )
                inbox = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
                assert inbox is not None
                if inbox.status == "completed":
                    return "Command already processed."
                if turn.target.target_kind != "agent":
                    raise ConflictError("Conversation commands require an Agent target")

                def factory() -> UUID:
                    return create_session(
                        session, actor=actor, instance_id=turn.target.target_id
                    ).id

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
                    result = "Conversation history reset."
                elif command == "new":
                    result = "Switched to a new conversation."
                else:
                    raise ConflictError("Unknown gateway command")
                # Command effects and reply intent commit together, making reconnects idempotent.
                inbox.status, inbox.target_id, inbox.user_id = (
                    "completed",
                    turn.target.target_id,
                    turn.user_id,
                )
                inbox.binding_revision = turn.binding_revision
                inbox.target_kind = turn.target.target_kind
                Outbox(GatewayStore(self.sessions, self.cipher)).enqueue(
                    session, turn, reply_parts(result)
                )
                return result

        return await asyncio.to_thread(apply)
