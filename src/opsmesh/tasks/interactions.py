from datetime import timedelta
from importlib.metadata import version
from typing import Any

from sqlalchemy.orm import Session

from opsmesh.contracts import Suspended, UserIdentity
from opsmesh.gateway.delivery import enqueue
from opsmesh.infra.database import now
from opsmesh.infra.encryption import SecretCipher
from opsmesh.sessions.models import AgentSession
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.users.service import identity

from .events import status
from .models import Task, TaskCheckpoint, TaskInteraction


def checkpoint(
    db: Session, cipher: SecretCipher, task: Task, attempt_id: str, state: dict[str, Any]
) -> TaskCheckpoint:
    conversation = db.get(AgentSession, task.session_id, with_for_update=True)
    if conversation is None or conversation.sandbox_dirty:
        raise ConflictError("Close and persist the sandbox before saving a checkpoint")
    row = TaskCheckpoint(
        task_id=task.id,
        attempt_id=attempt_id,
        sdk_version=version("openai-agents"),
        encrypted_state=cipher.encrypt(state),
        sandbox_generation=conversation.sandbox_generation,
    )
    db.add(row)
    db.flush()
    task.checkpoint_id = row.id
    task.decision_id = None
    return row


def validate_checkpoint(db: Session, task: Task, saved: TaskCheckpoint) -> None:
    conversation = db.get(AgentSession, task.session_id, with_for_update=True)
    if (
        conversation is None
        or conversation.closed
        or conversation.revision != task.session_revision
        or conversation.sandbox_dirty
        or conversation.sandbox_generation != saved.sandbox_generation
    ):
        raise ConflictError("Session or sandbox has changed since the checkpoint")


def suspend(
    db: Session, cipher: SecretCipher, task: Task, attempt_id: str, value: Suspended
) -> None:
    if not 1 <= value.timeout_seconds <= 604800 or value.kind not in {
        "approval",
        "question",
        "pause",
    }:
        raise ValueError("Invalid interaction kind or timeout")
    recipient = value.recipient_id or task.owner_id
    identity(db, recipient)
    saved = checkpoint(db, cipher, task, attempt_id, value.checkpoint)
    task.status = "waiting_input"
    row = TaskInteraction(
        task_id=task.id,
        checkpoint_id=saved.id,
        recipient_id=recipient,
        kind=value.kind,
        encrypted_prompt=cipher.encrypt({"prompt": value.prompt, "details": value.details}),
        expires_at=now() + timedelta(seconds=value.timeout_seconds),
    )
    db.add(row)
    db.flush()
    # Only notify the original caller's channel; a different recipient uses the API inbox.
    if recipient == task.owner_id:
        enqueue(
            db,
            cipher,
            task,
            f"interaction:{row.id}",
            f"{value.prompt}\nRequest: {row.id}",
            private=True,
        )


def respond(
    db: Session, cipher: SecretCipher, user: UserIdentity, request_id: str, value: Any
) -> TaskInteraction:
    item = db.get(TaskInteraction, request_id)
    if item is None or item.recipient_id != user.user_id:
        raise NotFoundError("TaskInteraction not found")
    task = db.get(Task, item.task_id, with_for_update=True)
    item = db.get(TaskInteraction, request_id, with_for_update=True, populate_existing=True)
    assert item is not None and task is not None
    if item.status == "answered":
        if cipher.decrypt(item.encrypted_decision or "") != value:
            raise ConflictError("TaskInteraction already answered")
        return item
    if (
        item.status != "pending"
        or item.expires_at <= now()
        or task.status != "waiting_input"
        or task.cancel_requested
        or task.checkpoint_id != item.checkpoint_id
    ):
        raise ConflictError("TaskInteraction is no longer awaiting a response")
    saved = db.get(TaskCheckpoint, item.checkpoint_id)
    assert saved is not None
    validate_checkpoint(db, task, saved)
    item.status, item.responder_id = "answered", user.user_id
    item.encrypted_decision = cipher.encrypt(value)
    task.decision_id, task.status = item.id, "queued"
    status(db, cipher, task)
    return item
