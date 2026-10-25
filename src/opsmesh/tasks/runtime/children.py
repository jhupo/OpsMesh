"""Nested agent calls with stable keys and bounded execution capacity."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.catalog.models import CatalogPublication
from opsmesh.infra.encryption import SecretCipher
from opsmesh.quotas.tokens import quota
from opsmesh.sessions.service import bind_session
from opsmesh.shared.errors import ConflictError
from opsmesh.users.service import identity

from ..models import Task
from .leases import TaskLeases


class ChildTaskPending(ConflictError):
    code = "child_task_pending"

    def __init__(self, child_id: str):
        super().__init__(f"Child task is awaiting completion: {child_id}")
        self.child_id = child_id


class ChildTasks:
    def __init__(
        self,
        leases: TaskLeases,
        cipher: SecretCipher,
        execute: Callable[[str, str], Awaitable[None]],
        concurrency: int,
    ):
        self.leases, self.cipher, self.execute = leases, cipher, execute
        self.slots = asyncio.BoundedSemaphore(concurrency)

    async def call(
        self,
        parent: Task,
        parent_attempt_id: str,
        publication: CatalogPublication,
        value: Any,
        key: str,
    ) -> Any:
        # Fail fast instead of waiting with a parent slot held by this execution tree.
        if self.slots.locked():
            raise ConflictError("Child execution capacity exhausted; limit parallel agent calls")
        async with self.slots:
            return await self._execute_child(parent, parent_attempt_id, publication, value, key)

    async def _execute_child(
        self,
        parent: Task,
        parent_attempt_id: str,
        publication: CatalogPublication,
        value: Any,
        key: str,
    ) -> Any:
        with self.leases.sessions.begin() as db:
            current_parent = self.leases.active(db, parent.id, parent_attempt_id)
            if self._depth(db, parent) >= 12:
                raise ConflictError("Nested agent depth exceeded")
            deduplication_key = f"child:{parent.id}:{key}"
            child = db.scalar(
                select(Task)
                .where(Task.owner_id == parent.owner_id, Task.idempotency_key == deduplication_key)
                .with_for_update()
            )
            if child is not None:
                previous = self.cipher.decrypt(child.encrypted_input)
                if previous["value"] != value or child.snapshot["id"] != publication.id:
                    raise ConflictError("Child invocation key was reused for different input")
                if child.status == "succeeded":
                    return self.cipher.decrypt(child.encrypted_result or "")
                if child.status in {"waiting_input", "waiting_child", "queued", "running"}:
                    self._pending(current_parent, child.id)
                if child.status in {"failed", "interrupted", "timed_out"}:
                    if child.checkpoint_id is None:
                        raise ConflictError(
                            f"Child task requires a checkpoint to recover: {child.id}"
                        )
                elif child.status != "succeeded":
                    raise ConflictError(f"Child task requires explicit recovery: {child.id}")
            else:
                child = Task(
                    owner_id=parent.owner_id,
                    parent_id=parent.id,
                    snapshot={
                        "id": publication.id,
                        "publication_id": publication.id,
                        "owner_id": publication.owner_id,
                        "plugin_id": publication.plugin_id,
                        "entry": publication.contribution,
                        "encrypted_config": publication.encrypted_config,
                        "dependencies": publication.dependencies,
                        "workspace_id": None,
                    },
                    encrypted_input=self.cipher.encrypt(
                        {"value": value, "metadata": {"parent_task_id": parent.id}}
                    ),
                    encrypted_source=self.cipher.encrypt({}),
                    timeout_seconds=parent.timeout_seconds,
                    idempotency_key=deduplication_key,
                )
                db.add(child)
                child_session = bind_session(
                    db, child.snapshot, identity(db, parent.owner_id), None, None
                )
                child.session_id = child_session.id
                db.flush()
            limits = quota(db, parent.owner_id)
            running_children = (
                db.scalar(
                    select(func.count())
                    .select_from(Task)
                    .where(
                        Task.owner_id == parent.owner_id,
                        Task.parent_id.is_not(None),
                        Task.status == "running",
                    )
                )
                or 0
            )
            if running_children >= limits.concurrent_tasks:
                raise ConflictError("User child execution quota exhausted")
            child_id, attempt_id = self.leases.start(db, child)
        await self.execute(child_id, attempt_id)
        with self.leases.sessions.begin() as db:
            child = db.get(Task, child_id)
            assert child is not None
            if child.status != "succeeded":
                if child.status in {"waiting_input", "waiting_child", "queued", "running"}:
                    current_parent = self.leases.active(db, parent.id, parent_attempt_id)
                    self._pending(current_parent, child.id)
                raise ConflictError(f"Child task ended with status {child.status}: {child.id}")
            return self.cipher.decrypt(child.encrypted_result or "")

    @staticmethod
    def _pending(parent: Task, child_id: str) -> None:
        if parent.checkpoint_id is None:
            raise ConflictError(
                f"Save a parent checkpoint before calling an interactive child: {child_id}"
            )
        raise ChildTaskPending(child_id)

    def _depth(self, db: Session, task: Task) -> int:
        depth = 0
        while task.parent_id:
            parent = db.get(Task, task.parent_id)
            if parent is None:
                raise ConflictError("Parent task missing")
            task, depth = parent, depth + 1
        return depth
