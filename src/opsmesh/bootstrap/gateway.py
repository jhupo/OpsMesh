"""Compose transport ports with authorized execution and workflow services."""

import asyncio
import base64
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.gateway.access import authorize_turn
from opsmesh.gateway.contracts import GatewayTurn, RouteDefinition
from opsmesh.gateway.media import GatewayMedia
from opsmesh.gateway.models import GatewayInbox
from opsmesh.gateway.sessions import mapped_session
from opsmesh.shared.errors import ConflictError
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

    def check(self, session: Session, user_id: UUID, route: RouteDefinition) -> Any:
        from opsmesh.execution.instances.access import current_user, get_instance
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
        content: list[dict[str, Any]] = []
        media = GatewayMedia(self.media_root / str(turn.channel_id))
        for part in turn.message.content:
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
                            input=[{"role": "user", "content": content}],
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
                            input={"messages": [{"role": "user", "content": content}]},
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

    async def control(self, turn: GatewayTurn, command: str) -> str:
        def apply() -> str:
            from opsmesh.execution.runs.models import ACTIVE, ExecutionRun
            from opsmesh.execution.runs.service import cancel_run
            from opsmesh.execution.sessions.service import create_session, reset_session
            from opsmesh.gateway.dispatcher import reply_parts
            from opsmesh.gateway.outbox import Outbox
            from opsmesh.gateway.store import GatewayStore
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
