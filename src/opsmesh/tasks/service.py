from dataclasses import asdict
from importlib.metadata import version
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from opsmesh.agents.service import snapshot, usable
from opsmesh.catalog.service import get as get_publication
from opsmesh.contracts import InvocationSource, UserIdentity
from opsmesh.infra.database import now
from opsmesh.infra.encryption import SecretCipher
from opsmesh.sessions.service import bind_session, release_session
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.users.service import require_owner

from .events import status
from .interactions import validate_checkpoint
from .models import Task, TaskCheckpoint, TaskInteraction

TERMINAL = frozenset({"succeeded", "failed", "cancelled", "interrupted", "timed_out"})


def submit(
    db: Session,
    cipher: SecretCipher,
    user: UserIdentity,
    agent_id: str,
    value: Any,
    *,
    metadata: dict[str, Any] | None = None,
    source: InvocationSource | None = None,
    idempotency_key: str | None = None,
    timeout: int = 600,
    schedule_id: str | None = None,
    session_id: str | None = None,
) -> Task:
    agent = usable(db, user, agent_id)
    return _enqueue(
        db,
        cipher,
        user,
        snapshot(agent),
        value,
        agent_id=agent_id,
        metadata=metadata,
        source=source,
        idempotency_key=idempotency_key,
        timeout=timeout,
        schedule_id=schedule_id,
        session_id=session_id,
    )


def submit_publication(
    db: Session,
    cipher: SecretCipher,
    user: UserIdentity,
    publication_id: str,
    value: Any,
    *,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    timeout: int = 600,
    session_id: str | None = None,
) -> Task:
    publication = get_publication(db, user, publication_id)
    if publication.kind != "agent":
        raise ConflictError("Publication is not an agent")
    frozen = {
        "id": publication.id,
        "publication_id": publication.id,
        "owner_id": publication.owner_id,
        "plugin_id": publication.plugin_id,
        "entry": publication.contribution,
        "encrypted_config": publication.encrypted_config,
        "dependencies": publication.dependencies,
        "workspace_id": None,
    }
    return _enqueue(
        db,
        cipher,
        user,
        frozen,
        value,
        metadata=metadata,
        idempotency_key=idempotency_key,
        timeout=timeout,
        session_id=session_id,
    )


def _enqueue(
    db: Session,
    cipher: SecretCipher,
    user: UserIdentity,
    frozen: dict[str, Any],
    value: Any,
    *,
    agent_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    source: InvocationSource | None = None,
    idempotency_key: str | None = None,
    timeout: int = 600,
    schedule_id: str | None = None,
    session_id: str | None = None,
) -> Task:
    if idempotency_key:
        db.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(f"task:{user.user_id}:{idempotency_key}", 0)
                )
            )
        )
        old = db.scalar(
            select(Task).where(
                Task.owner_id == user.user_id, Task.idempotency_key == idempotency_key
            )
        )
        if old is not None:
            if (
                old.snapshot["id"] != frozen["id"]
                or old.agent_id != agent_id
                or old.timeout_seconds != timeout
                or (session_id is not None and old.session_id != session_id)
                or cipher.decrypt(old.encrypted_input)
                != {"value": value, "metadata": metadata or {}}
            ):
                raise ConflictError("Idempotency key was used for a different request")
            return old
    conversation = bind_session(db, frozen, user, source, session_id)
    task = Task(
        owner_id=user.user_id,
        session_id=conversation.id,
        agent_id=agent_id,
        snapshot=frozen,
        encrypted_input=cipher.encrypt({"value": value, "metadata": metadata or {}}),
        encrypted_source=cipher.encrypt(asdict(source or InvocationSource())),
        idempotency_key=idempotency_key,
        timeout_seconds=timeout,
        schedule_id=schedule_id,
    )
    db.add(task)
    db.flush()
    status(db, cipher, task)
    return task


def owned(db: Session, user: UserIdentity, task_id: str, *, lock: bool = False) -> Task:
    row = db.get(Task, task_id, with_for_update=lock)
    if row is None:
        raise NotFoundError("Task not found")
    require_owner(user, row.owner_id)
    return row


def cancel(db: Session, cipher: SecretCipher, user: UserIdentity, task_id: str) -> Task:
    row = owned(db, user, task_id, lock=True)
    changed = row.status not in TERMINAL and not row.cancel_requested
    if row.status not in TERMINAL:
        row.cancel_requested = True
    if row.status in {"queued", "waiting_input", "waiting_child"}:
        row.status, row.finished_at = "cancelled", now()
        release_session(db, row.id, row.session_id)
        db.execute(
            update(TaskInteraction)
            .where(TaskInteraction.task_id == row.id, TaskInteraction.status == "pending")
            .values(status="cancelled")
        )
    if changed:
        status(db, cipher, row)
    parents = [row.id]
    while parents:
        children = list(
            db.scalars(
                select(Task).where(Task.parent_id.in_(parents)).order_by(Task.id).with_for_update()
            )
        )
        parents = [child.id for child in children]
        for child in children:
            if child.status not in TERMINAL and not child.cancel_requested:
                child.cancel_requested = True
                if child.status in {"queued", "waiting_input", "waiting_child"}:
                    child.status, child.finished_at = "cancelled", now()
                    release_session(db, child.id, child.session_id)
                    db.execute(
                        update(TaskInteraction)
                        .where(
                            TaskInteraction.task_id == child.id,
                            TaskInteraction.status == "pending",
                        )
                        .values(status="cancelled")
                    )
                status(db, cipher, child)
    return row


def resume(db: Session, cipher: SecretCipher, user: UserIdentity, task_id: str) -> Task:
    row = owned(db, user, task_id, lock=True)
    checkpoint = db.get(TaskCheckpoint, row.checkpoint_id) if row.checkpoint_id else None
    if (
        row.status not in {"interrupted", "failed", "timed_out"}
        or checkpoint is None
        or row.cancel_requested
    ):
        raise ConflictError("Task has no resumable checkpoint")
    if checkpoint.sdk_version != version("openai-agents"):
        raise ConflictError("Checkpoint SDK version differs from the runtime")
    validate_checkpoint(db, row, checkpoint)
    row.status, row.error_code, row.finished_at = "queued", None, None
    status(db, cipher, row)
    return row
