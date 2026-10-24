"""Coordinate one attempt's execution, cancellation and cleanup."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from opsmesh_sdk import Suspended
from sqlalchemy.orm import sessionmaker

from opsmesh.gateway.delivery import enqueue
from opsmesh.infra.database import now
from opsmesh.infra.encryption import SecretCipher
from opsmesh.plugins.registry import PluginRegistry
from opsmesh.quotas.config import ModelDefaults
from opsmesh.sessions.service import release_session
from opsmesh.shared.context import LogContext, logging_context
from opsmesh.shared.errors import DomainError

from ..events import append, status
from ..interactions import suspend
from ..models import Task, TaskAttempt
from .children import ChildTasks
from .invocation import TaskInvoker
from .leases import TaskLeaseLost, TaskLeases

logger = logging.getLogger(__name__)


class TaskRunner:
    def __init__(
        self,
        sessions: sessionmaker,
        cipher: SecretCipher,
        registry: PluginRegistry,
        root: Path,
        *,
        lease_seconds: int = 90,
        model_defaults: ModelDefaults | None = None,
        session_ttl_seconds: int = 31_536_000,
        encryption_key: str,
        sandbox_root: str = "/workspace",
        child_concurrency: int = 1,
    ):
        self.sessions, self.cipher = sessions, cipher
        self.leases = TaskLeases(sessions, cipher, lease_seconds)
        self.children = ChildTasks(self.leases, cipher, self.execute, child_concurrency)
        self.invoker = TaskInvoker(
            sessions,
            cipher,
            registry,
            root,
            self.leases,
            self.children,
            model_defaults=model_defaults or ModelDefaults(),
            session_ttl_seconds=session_ttl_seconds,
            encryption_key=encryption_key,
            sandbox_root=sandbox_root,
        )

    async def execute(self, task_id: str, attempt_id: str) -> None:
        logging_token = logging_context.set(LogContext(task_id=task_id, attempt_id=attempt_id))
        execution = asyncio.create_task(self.invoker.invoke(task_id, attempt_id))
        heartbeat = asyncio.create_task(self.leases.heartbeat(task_id, attempt_id, execution))
        failure: tuple[str, str] | None = None
        try:
            with self.sessions.begin() as db:
                task = self.leases.active(db, task_id, attempt_id)
                timeout = task.timeout_seconds
            async with asyncio.timeout(timeout):
                done, _ = await asyncio.wait(
                    {execution, heartbeat}, return_when=asyncio.FIRST_COMPLETED
                )
                if heartbeat in done:
                    await heartbeat
                result = await execution
            self._complete(task_id, attempt_id, result)
        except TaskLeaseLost:
            logger.warning("task_lease_lost")
        except TimeoutError:
            failure = ("timed_out", "execution_timeout")
        except asyncio.CancelledError:
            with self.sessions.begin() as db:
                task = db.get(Task, task_id)
                requested = task is not None and task.cancel_requested
            failure = (
                "cancelled" if requested else "interrupted",
                "cancelled" if requested else "worker_stopped",
            )
            raise
        except Exception as exc:
            failure = (
                "failed",
                exc.code if isinstance(exc, DomainError) else type(exc).__name__,
            )
            logger.exception("task_failed")
        finally:
            try:
                if not execution.done():
                    execution.cancel()
                cleanup = asyncio.gather(execution, return_exceptions=True)
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
                    raise
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                if failure:
                    self.leases.fail(task_id, attempt_id, *failure)
                logging_context.reset(logging_token)

    def _complete(self, task_id: str, attempt_id: str, result: Any) -> None:
        with self.sessions.begin() as db:
            task = self.leases.active(db, task_id, attempt_id)
            attempt = db.get(TaskAttempt, attempt_id)
            assert attempt is not None
            if isinstance(result, Suspended):
                suspend(db, self.cipher, task, attempt_id, result)
            else:
                task.encrypted_result = self.cipher.encrypt(result)
                task.status, task.finished_at = "succeeded", now()
                append(db, self.cipher, task, "task.result", result)
                enqueue(
                    db,
                    self.cipher,
                    task,
                    f"result:{attempt_id}",
                    result
                    if isinstance(result, str)
                    else "Task completed. Results are available in OpsMesh.",
                )
            attempt.status, attempt.finished_at = task.status, now()
            if task.status != "waiting_input":
                release_session(db, task.id, task.session_id)
            status(db, self.cipher, task)
