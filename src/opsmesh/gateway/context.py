import hashlib
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from opsmesh.infra.encryption import SecretCipher
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.tasks.models import Task
from opsmesh.users.service import identity, require_admin

from .delivery import enqueue as enqueue_reply
from .operations import enqueue


class InvocationChannel:
    def __init__(
        self, sessions: sessionmaker, cipher: SecretCipher, validate: Callable[[Session], Task]
    ):
        self.sessions, self.cipher, self.validate = sessions, cipher, validate

    @staticmethod
    def key(value: str) -> str:
        if not value or len(value) > 80:
            raise InvalidArgumentError("Channel key must contain 1 to 80 characters")
        return f"plugin:{value}"

    async def reply(
        self, text: str = "", *, content: list[dict[str, Any]] | None = None, key: str
    ) -> str:
        with self.sessions.begin() as db:
            task = self.validate(db)
            identity(db, task.owner_id)
            delivery_id = enqueue_reply(db, self.cipher, task, self.key(key), text, content=content)
            if delivery_id is None:
                raise ConflictError("Task has no channel")
            return delivery_id

    async def request(self, operation: str, parameters: dict[str, Any], *, key: str) -> str:
        with self.sessions.begin() as db:
            task = self.validate(db)
            require_admin(identity(db, task.owner_id))
            source = self.cipher.decrypt(task.encrypted_source)
            if not source.get("channel_id"):
                raise ConflictError("Task has no channel")
            return enqueue(
                db,
                self.cipher,
                source["channel_id"],
                task.owner_id,
                operation,
                parameters,
                hashlib.sha256(f"{task.id}:{self.key(key)}".encode()).hexdigest(),
            ).id
