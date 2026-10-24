"""Direct execution of persisted runs with native SDK resource lifespans."""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from agents.memory.session import Session as NativeSession
from agents.sandbox import SandboxRunConfig
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.agents.assembly import AssemblyBindings
from opsmesh.execution.bindings import execution_bindings, freeze_bindings
from opsmesh.execution.context import ExecutionContext, serialize_execution_context
from opsmesh.execution.contracts import HistoryCall, PreparedAgent
from opsmesh.execution.runner import run_agent
from opsmesh.execution.runs.access import authorize_run
from opsmesh.execution.runs.models import ExecutionRun
from opsmesh.execution.runs.operations import OperationLedger
from opsmesh.execution.runs.schemas import RunCreate
from opsmesh.execution.runs.service import claim_run, finish_run
from opsmesh.execution.runtime.sandbox import AgentSandboxClient, AgentSandboxState
from opsmesh.execution.sessions.service import get_session
from opsmesh.execution.sessions.storage import HistorySession, NativeHistory
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.users.service import load_identity

BindingsFactory = Callable[
    [ExecutionContext, PreparedAgent], AssemblyBindings | Awaitable[AssemblyBindings]
]
logger = logging.getLogger(__name__)


class RunExecutor:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        cipher: SecretCipher,
        *,
        bindings: BindingsFactory,
        history_root: Path,
        sandbox: AgentSandboxClient | None = None,
    ) -> None:
        self.sessions, self.cipher = sessions, cipher
        self.bindings, self.history_root, self.sandbox = bindings, history_root, sandbox

    async def _history(
        self, prepared: PreparedAgent, ledger: OperationLedger
    ) -> NativeSession | None:
        if prepared.session is None:
            return None

        async def authorize(call: HistoryCall) -> None:
            with self.sessions.begin() as session:
                run = authorize_run(session, call.identity.run_id)
                actor = load_identity(session, run.user_id)
                assert actor is not None
                conversation = get_session(
                    session, actor=actor, session_id=call.reference.session_id
                )
                if (
                    run.session_id != conversation.id
                    or run.generation != conversation.generation
                    or call.reference.generation != conversation.generation
                ):
                    raise ConflictError("The private history generation changed")

        class RecordedHistory(NativeHistory):
            async def operate(self, request: HistoryCall) -> object:
                if request.method == "get_items":
                    return await super().operate(request)
                operation_id = ledger.context.operation_id("history", uuid4().hex)
                ledger.claim(operation_id, "history", request.model_dump(mode="json"))
                try:
                    value = await super().operate(request)
                    ledger.complete(operation_id, {"value": value})
                    return value
                except BaseException:
                    ledger.uncertain(operation_id)
                    raise

        return HistorySession(
            RecordedHistory(self.history_root, authorize=authorize),
            prepared.identity,
            prepared.session,
        )

    async def execute(
        self, run_id: UUID, *, on_event: Callable[[object], Awaitable[None]] | None = None
    ) -> None:
        with self.sessions.begin() as session:
            context, prepared, value, state, decision = claim_run(
                session, run_id=run_id, driver="direct", cipher=self.cipher
            )
            row = session.get(ExecutionRun, run_id)
            assert row is not None
            request = RunCreate.model_validate(self.cipher.decrypt_payload(row.encrypted_input))
        ledger = OperationLedger(self.sessions, self.cipher, context)

        async def authorized() -> None:
            ledger.authorize()

        async def invoke() -> Any:
            nonlocal prepared
            source = self.bindings(context, prepared)
            if inspect.isawaitable(source):
                source = await source
            prepared = freeze_bindings(source, ledger, prepared)
            if any(config.model is None for config in prepared.snapshot.definitions.values()):
                raise ConflictError(
                    "Execution Agent configurations require an authorized model reference"
                )
            sandbox = None
            if prepared.environment_id is not None:
                if self.sandbox is None:
                    raise ConflictError("The sandbox executor is not configured")
                sandbox = SandboxRunConfig(
                    client=self.sandbox,
                    session_state=AgentSandboxState(
                        session_id=run_id,
                        environment_id=prepared.environment_id,
                    ),
                    cwd=".",
                )
            async with asyncio.timeout(request.timeout_seconds):
                return await run_agent(
                    prepared,
                    value,
                    bindings=execution_bindings(source, ledger),
                    context=context,
                    session=await self._history(prepared, ledger),
                    sandbox=sandbox,
                    state_json=state,
                    decision=decision,
                    authorize=authorized,
                    on_event=on_event,
                )

        task = asyncio.create_task(invoke())

        async def watch_cancellation() -> None:
            while not task.done():
                await asyncio.sleep(0.5)
                with self.sessions.begin() as session:
                    row = session.get(ExecutionRun, run_id)
                    if row is None or row.status == "cancelling":
                        task.cancel()
                        return

        watcher = asyncio.create_task(watch_cancellation())
        try:
            done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                # A failed cancellation monitor cannot leave the native run unobserved.
                await watcher
            result = await task
            with self.sessions.begin() as session:
                finish_run(
                    session,
                    context=context,
                    status=result.status,
                    cipher=self.cipher,
                    output=result.output,
                    native_state=result.state.to_json(
                        context_serializer=serialize_execution_context
                    )
                    if result.state
                    else None,
                    pending=result.pending,
                )
        except BaseException as error:
            if not isinstance(error, asyncio.CancelledError):
                logger.exception("Agent run failed", extra={"run_id": str(run_id)})
            # Complete all owned work before releasing admission or acknowledging cancellation.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            with self.sessions.begin() as session:
                finish_run(
                    session,
                    context=context,
                    cipher=self.cipher,
                    status="cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
                    error_code="timeout" if isinstance(error, TimeoutError) else "execution_failed",
                )
            if isinstance(error, asyncio.CancelledError):
                raise
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
