"""Short database reads for authenticated, resumable task subscriptions."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from opsmesh.infra.encryption import SecretCipher
from opsmesh.users.service import authenticate

from .models import TaskEvent
from .schemas import TaskDetailsView
from .service import TERMINAL, owned


@dataclass(frozen=True)
class EventPage:
    events: list[dict[str, Any]]
    snapshot: dict[str, Any]
    latest_sequence: int
    stopped: bool


class EventSubscription:
    def __init__(
        self, sessions: sessionmaker, cipher: SecretCipher, token: str, task_id: str, after: int
    ):
        self.sessions, self.cipher = sessions, cipher
        self.token, self.task_id, self.after = token, task_id, after

    def read(self) -> EventPage:
        with self.sessions.begin() as db:
            user = authenticate(db, self.token)
            task = owned(db, user, self.task_id)
            if self.after > task.event_sequence:
                raise ValueError("Event cursor is ahead of the task")
            events = [
                {
                    "sequence": row.sequence,
                    "event": row.event,
                    "data": self.cipher.decrypt(row.encrypted_data),
                }
                for row in db.scalars(
                    select(TaskEvent)
                    .where(
                        TaskEvent.task_id == task.id,
                        TaskEvent.sequence > self.after,
                        TaskEvent.sequence <= task.event_sequence,
                    )
                    .order_by(TaskEvent.sequence)
                    .limit(100)
                )
            ]
            snapshot = TaskDetailsView.model_validate(task).model_dump(mode="json")
            if task.encrypted_result is not None:
                snapshot["result"] = self.cipher.decrypt(task.encrypted_result)
            return EventPage(
                events,
                snapshot,
                task.event_sequence,
                task.status in TERMINAL or task.status == "waiting_input",
            )
