import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from opsmesh.infra.database import now
from opsmesh.infra.encryption import SecretCipher
from opsmesh.sessions.service import release_session
from opsmesh.shared.errors import DomainError
from opsmesh.users.service import identity

from .events import status
from .models import Task, TaskInteraction, TaskSchedule
from .schedules import next_time
from .service import TERMINAL, submit

logger = logging.getLogger(__name__)


def tick(sessions: sessionmaker, cipher: SecretCipher) -> None:
    with sessions.begin() as db:
        due = db.scalars(
            select(TaskSchedule)
            .where(TaskSchedule.enabled.is_(True), TaskSchedule.next_at <= now())
            .order_by(TaskSchedule.next_at)
            .with_for_update(skip_locked=True)
            .limit(50)
        )
        for schedule in due:
            planned = schedule.next_at
            following = next_time(schedule, now())
            if following is None:
                schedule.enabled = False
            else:
                schedule.next_at = following
            active = db.get(Task, schedule.last_task_id) if schedule.last_task_id else None
            if active is not None and active.status not in TERMINAL:
                continue
            try:
                with db.begin_nested():
                    task = submit(
                        db,
                        cipher,
                        identity(db, schedule.owner_id),
                        schedule.agent_id,
                        cipher.decrypt(schedule.encrypted_input),
                        idempotency_key=f"schedule:{schedule.id}:{planned.isoformat()}",
                        schedule_id=schedule.id,
                    )
                    schedule.last_task_id = task.id
                    schedule.error_code = None
            except DomainError as exc:
                schedule.enabled, schedule.error_code = False, exc.code
        expired = db.scalars(
            select(TaskInteraction)
            .where(TaskInteraction.status == "pending", TaskInteraction.expires_at <= now())
            .limit(100)
        )
        for item in expired:
            task = db.get(Task, item.task_id, with_for_update=True)
            item = db.get(TaskInteraction, item.id, with_for_update=True, populate_existing=True)
            if item is None or item.status != "pending" or item.expires_at > now():
                continue
            if task is not None and task.status == "waiting_input":
                task.status, task.error_code, task.finished_at = (
                    "timed_out",
                    "interaction_expired",
                    now(),
                )
                release_session(db, task.id, task.session_id)
                status(db, cipher, task)
            item.status = "expired"


async def serve(sessions: sessionmaker, cipher: SecretCipher) -> None:
    while True:
        try:
            tick(sessions, cipher)
        except Exception:
            logger.exception("scheduler_tick_failed")
        await asyncio.sleep(1)
