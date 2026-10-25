"""Task claiming, attempt ownership and lease recovery."""

import asyncio
from datetime import timedelta
from typing import Any

from agents import gen_trace_id
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from opsmesh.infra.database import new_id, now
from opsmesh.infra.encryption import SecretCipher
from opsmesh.quotas.models import UserQuota
from opsmesh.quotas.tokens import quota
from opsmesh.sessions.models import AgentSession
from opsmesh.sessions.service import release_session
from opsmesh.shared.errors import ConflictError

from ..events import status
from ..models import Task, TaskAttempt
from ..service import TERMINAL


class TaskLeaseLost(ConflictError):
    code = "task_lease_lost"


class TaskLeases:
    def __init__(self, sessions: sessionmaker, cipher: SecretCipher, lease_seconds: int = 90):
        self.sessions, self.cipher = sessions, cipher
        self.lease_seconds, self.worker_id = lease_seconds, new_id()

    def claim(self) -> tuple[str, str] | None:
        with self.sessions.begin() as db:
            running = aliased(Task)
            running_count = (
                select(func.count())
                .select_from(running)
                .where(
                    running.owner_id == Task.owner_id,
                    running.status == "running",
                    or_(
                        and_(Task.parent_id.is_(None), running.parent_id.is_(None)),
                        and_(Task.parent_id.is_not(None), running.parent_id.is_not(None)),
                    ),
                )
                .correlate(Task)
                .scalar_subquery()
            )
            parent = aliased(Task)
            tasks = db.scalars(
                select(Task)
                .outerjoin(UserQuota, UserQuota.user_id == Task.owner_id)
                .where(
                    Task.status == "queued",
                    or_(
                        Task.parent_id.is_(None),
                        select(parent.id)
                        .where(
                            parent.id == Task.parent_id,
                            parent.status == "waiting_child",
                        )
                        .exists(),
                    ),
                    running_count < func.coalesce(UserQuota.concurrent_tasks, 4),
                    or_(
                        Task.session_id.is_(None),
                        select(AgentSession.id)
                        .where(
                            AgentSession.id == Task.session_id,
                            or_(
                                AgentSession.sandbox_dirty.is_(False),
                                AgentSession.sandbox_task_id == Task.id,
                            ),
                            or_(
                                AgentSession.active_task_id.is_(None),
                                AgentSession.active_task_id == Task.id,
                            ),
                        )
                        .exists(),
                    ),
                )
                .order_by(Task.created_at)
                .with_for_update(skip_locked=True, of=Task)
                .limit(50)
            )
            for task in tasks:
                if task.session_id:
                    conversation = db.scalar(
                        select(AgentSession)
                        .where(AgentSession.id == task.session_id)
                        .with_for_update(skip_locked=True)
                    )
                    if conversation is None:
                        continue
                    if conversation.closed:
                        task.status, task.error_code, task.finished_at = (
                            "cancelled",
                            "session_closed",
                            now(),
                        )
                        status(db, self.cipher, task)
                        continue
                    if conversation.active_task_id and conversation.active_task_id != task.id:
                        continue
                    if conversation.sandbox_dirty and conversation.sandbox_task_id != task.id:
                        continue
                    if (
                        task.session_revision is not None
                        and task.session_revision != conversation.revision
                    ):
                        task.status, task.error_code, task.finished_at = (
                            "failed",
                            "session_advanced",
                            now(),
                        )
                        status(db, self.cipher, task)
                        continue
                limits = quota(db, task.owner_id)
                count = (
                    db.scalar(
                        select(func.count())
                        .select_from(Task)
                        .where(
                            Task.owner_id == task.owner_id,
                            Task.status == "running",
                            Task.parent_id.is_(None)
                            if task.parent_id is None
                            else Task.parent_id.is_not(None),
                        )
                    )
                    or 0
                )
                if count >= limits.concurrent_tasks:
                    continue
                return self.start(db, task)
        return None

    def start(self, db: Session, task: Task) -> tuple[str, str]:
        if task.session_id:
            conversation = db.get(AgentSession, task.session_id, with_for_update=True)
            if conversation is None or conversation.closed:
                raise ConflictError("Session unavailable")
            if conversation.active_task_id not in {None, task.id}:
                raise ConflictError("Session already has an active turn")
            if conversation.sandbox_dirty and conversation.sandbox_task_id != task.id:
                raise ConflictError("Session has an unresolved sandbox turn")
            if task.session_revision is None:
                conversation.revision += 1
                task.session_revision = conversation.revision
            elif task.session_revision != conversation.revision:
                raise ConflictError("Session has advanced beyond this task's turn")
            conversation.active_task_id = task.id
        task.status, task.attempt_count = "running", task.attempt_count + 1
        task.error_code, task.finished_at = None, None
        task.waiting_child_id = None
        attempt = TaskAttempt(
            task_id=task.id,
            number=task.attempt_count,
            worker_id=self.worker_id,
            trace_id=gen_trace_id(),
            lease_until=now() + timedelta(seconds=self.lease_seconds),
        )
        db.add(attempt)
        db.flush()
        status(db, self.cipher, task)
        return task.id, attempt.id

    def wake_parents(self) -> None:
        with self.sessions.begin() as db:
            child = aliased(Task)
            parents = db.scalars(
                select(Task)
                .join(child, child.id == Task.waiting_child_id)
                .where(Task.status == "waiting_child", child.status.in_(TERMINAL))
                .order_by(Task.created_at)
                .with_for_update(skip_locked=True, of=Task)
                .limit(100)
            )
            for parent in parents:
                # This queues the saved continuation; invoke validates its SDK and session.
                parent.status = "queued"
                parent.waiting_child_id = None
                status(db, self.cipher, parent)

    def active(self, db: Session, task_id: str, attempt_id: str, *, cleanup: bool = False) -> Task:
        task = db.get(Task, task_id, with_for_update=True)
        attempt = db.get(TaskAttempt, attempt_id, with_for_update=True)
        if (
            task is None
            or attempt is None
            or task.status != "running"
            or attempt.status != "running"
            or attempt.task_id != task_id
            or attempt.number != task.attempt_count
            or attempt.worker_id != self.worker_id
            or attempt.lease_until <= now()
        ):
            raise TaskLeaseLost("Task execution lease was lost")
        if task.cancel_requested and not cleanup:
            raise asyncio.CancelledError()
        return task

    async def heartbeat(self, task_id: str, attempt_id: str, execution: asyncio.Task[Any]) -> None:
        cancelled = False
        while True:
            await asyncio.sleep(self.lease_seconds / 3)
            with self.sessions.begin() as db:
                task = self.active(db, task_id, attempt_id, cleanup=True)
                attempt = db.get(TaskAttempt, attempt_id)
                assert attempt is not None
                attempt.lease_until = now() + timedelta(seconds=self.lease_seconds)
                if task.cancel_requested and not cancelled:
                    execution.cancel()
                    cancelled = True

    def fail(self, task_id: str, attempt_id: str, outcome: str, code: str) -> None:
        with self.sessions.begin() as db:
            try:
                task = self.active(db, task_id, attempt_id, cleanup=True)
            except TaskLeaseLost:
                return
            attempt = db.get(TaskAttempt, attempt_id)
            assert attempt is not None
            task.status, task.error_code, task.finished_at = outcome, code[:100], now()
            attempt.status, attempt.finished_at = outcome, now()
            release_session(db, task.id, task.session_id)
            status(db, self.cipher, task)


def recover_expired(sessions: sessionmaker, cipher: SecretCipher) -> None:
    with sessions.begin() as db:
        rows = db.scalars(
            select(TaskAttempt)
            .where(TaskAttempt.status == "running", TaskAttempt.lease_until < now())
            .limit(100)
        )
        for attempt in rows:
            task = db.get(Task, attempt.task_id, with_for_update=True)
            attempt = db.get(TaskAttempt, attempt.id, with_for_update=True, populate_existing=True)
            if (
                attempt is not None
                and attempt.status == "running"
                and attempt.lease_until < now()
                and task is not None
                and task.status == "running"
                and task.attempt_count == attempt.number
            ):
                task.status, task.error_code, task.finished_at = (
                    "interrupted",
                    "worker_lease_expired",
                    now(),
                )
                attempt.status, attempt.finished_at = "interrupted", now()
                release_session(db, task.id, task.session_id)
                status(db, cipher, task)
