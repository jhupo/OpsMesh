"""Ordered, encrypted task events shared by workers and SSE readers."""

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from opsmesh.infra.encryption import SecretCipher
from opsmesh.shared.errors import QuotaExceededError

from .models import Task, TaskEvent


def append(db: Session, cipher: SecretCipher, task: Task, event: str, data: Any) -> int:
    """Append while the caller holds the task row lock, in its state transaction."""
    size = len(json.dumps(data, ensure_ascii=True, allow_nan=False).encode())
    task.event_sequence += 1
    task.event_bytes += size
    db.add(
        TaskEvent(
            task_id=task.id,
            sequence=task.event_sequence,
            event=event,
            encrypted_data=cipher.encrypt(data),
        )
    )
    return task.event_sequence


def status(db: Session, cipher: SecretCipher, task: Task) -> None:
    append(
        db,
        cipher,
        task,
        "task.status",
        {
            "status": task.status,
            "attempt_count": task.attempt_count,
            "cancel_requested": task.cancel_requested,
            "error_code": task.error_code,
        },
    )


def plugin_event(db: Session, cipher: SecretCipher, task: Task, event: str, data: Any) -> int:
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", event):
        raise ValueError("Invalid plugin event name")
    size = len(json.dumps(data, ensure_ascii=True, allow_nan=False).encode())
    if size > 65536 or task.event_sequence >= 10000 or task.event_bytes + size > 8_000_000:
        raise QuotaExceededError("Task progress event limit exceeded")
    return append(db, cipher, task, f"plugin.{event}", data)
