"""A Session belongs to one blocking operation and never leaves that operation."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from sqlalchemy import event
from sqlalchemy.orm import Session

from backend.app.shared.concurrency import BlockingIO

T = TypeVar("T")


@dataclass(frozen=True)
class DatabaseOperations:
    session_factory: Callable[[], Session]
    io: BlockingIO
    guard: Callable[[], None]

    async def run(self, operation: Callable[[Session], T]) -> T:
        return await self.io.run(lambda: self.execute(operation))

    def execute(self, operation: Callable[[Session], T]) -> T:
        with self.session_factory() as session:
            event.listen(session, "before_commit", lambda _: self.guard())
            try:
                self.guard()
                result = operation(session)
                self.guard()
                session.commit()
                return result
            except BaseException:
                session.rollback()
                raise
