"""Independent supervised lanes for moderation, batched replies and conservative activity."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import partial

from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.bots.conversations import ConversationBatch, ConversationInbox

from .activity import ConversationActivity
from .chat import ConversationChatWorker
from .configuration import ConversationBinding, ConversationOptions
from .contracts import ConversationPolicy
from .media import ConversationMedia
from .moderation import ConversationModerationWorker
from .operations import ConversationOperations

logger = logging.getLogger(__name__)
_SAFE_CODE = re.compile(r"[A-Z0-9_.:-]{1,120}\Z")
_DURABLE_WAIT_CODES = frozenset(
    {
        "CONVERSATION_RUN_WAITING_APPROVAL",
        "CONVERSATION_RUN_TIMEOUT",
        "CONVERSATION_COMMIT_PENDING",
    }
)
_TRANSIENT_RETRY_LIMIT = 8


def _error_code(error: Exception) -> str:
    if isinstance(error, OpsMeshError) and _SAFE_CODE.fullmatch(error.code):
        return error.code
    return "CONVERSATION_UNEXPECTED_ERROR"


def _backoff(failures: int) -> float:
    return float(min(30.0, 0.5 * 2 ** min(failures - 1, 6)))


class ConversationCoordinator:
    def __init__(
        self,
        settings: ConversationOptions,
        queue: ConversationInbox,
        platform: ConversationOperations,
        moderation: ConversationPolicy,
    ) -> None:
        self.settings, self.queue, self.platform = settings, queue, platform
        self.moderation_worker = ConversationModerationWorker(settings, queue, platform, moderation)
        self.chat_worker = ConversationChatWorker(settings, queue, platform, moderation)
        self.activity = ConversationActivity(settings, queue, platform, moderation)
        self._group_locks = {group.key: asyncio.Lock() for group in settings.bindings}
        self._tasks: list[asyncio.Task[None]] = []
        self._capture_cursors: dict[str, str | None] = {}

    def start(self, driver: OpsMeshDriver) -> None:
        """Start in the Driver startup hook; the caller owns queue and client shutdown."""
        if any(not task.done() for task in self._tasks):
            raise RuntimeError("Conversation coordinator has already started")
        self._tasks.clear()
        self._start_lane(driver, "moderation", self.moderation_worker.tick)
        if self.settings.images_enabled:
            self._start_lane(driver, "media", self.moderation_worker.media_tick)
        self._start_lane(driver, "maintenance", self.maintenance_tick, interval=3600)
        for group in self.settings.bindings:
            suffix = hashlib.sha256(group.key.encode()).hexdigest()[:12]
            self._start_lane(driver, f"chat:{suffix}", partial(self.chat_tick, group))
            self._start_lane(
                driver, f"activity:{suffix}", partial(self.activity_tick, group), interval=30
            )
            self._start_lane(
                driver, f"capture:{suffix}", partial(self.capture_tick, group), interval=30
            )

    def _start_lane(
        self,
        driver: OpsMeshDriver,
        name: str,
        callback: Callable[[], Awaitable[None]],
        *,
        interval: float | None = None,
    ) -> None:
        self._tasks.append(
            driver.create_task(
                self._poll(name, callback, interval or self.settings.poll_seconds),
                name="conversation:" + name,
            )
        )

    async def close(self) -> None:
        """Cancel only local waiters, leaving durable runs and the queue intact."""
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _poll(name: str, callback: Callable[[], Awaitable[None]], interval: float) -> None:
        failures = 0
        while True:
            try:
                await callback()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures += 1
                logger.warning("conversation.lane_failed lane=%s code=%s", name, _error_code(error))
                delay = min(30.0, max(interval, _backoff(failures)))
            else:
                failures = 0
                delay = interval
            await asyncio.sleep(delay)

    async def chat_tick(self, group: ConversationBinding) -> None:
        debounce = True
        while True:
            async with self._group_locks[group.key]:
                pending = await self.queue.pending_batches()
                batch = next(
                    (
                        item
                        for item in pending
                        if (item.bot_id, item.channel_id) == (group.bot_id, group.channel_id)
                    ),
                    None,
                )
                if batch is None:
                    if (group.bot_id, group.channel_id) not in await self.queue.ready_channels():
                        return
                    if debounce:
                        await asyncio.sleep(self.settings.batch_delay_seconds)
                    batch = await self.queue.claim_batch(
                        group.bot_id,
                        group.channel_id,
                        max_chars=self.settings.batch_characters,
                    )
                if batch is None or not await self._process_batch(group, batch):
                    return
            debounce = False

    async def _process_batch(self, group: ConversationBinding, batch: ConversationBatch) -> bool:
        retry_key = "conversation-retry:" + batch.batch_id
        saved = await self.queue.get_value(retry_key)
        if saved and datetime.fromisoformat(str(saved["next_attempt_at"])) > datetime.now(UTC):
            return False
        try:
            await self.chat_worker.process(group, batch)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = _error_code(error)
            current = await self.queue.get_batch(batch.batch_id)
            if current.state in {"claimed", "running"}:
                if await self._defer_batch(batch.batch_id, retry_key, error):
                    raise
                await self.queue.mark_failed(batch.batch_id, code)
                logger.warning("conversation.chat_failed code=%s", code)
        await self.queue.delete_value(retry_key)
        return True

    async def _defer_batch(self, batch_id: str, key: str, error: Exception) -> bool:
        """Keep durable waits active; bound safe transient retries without replaying uncertainty."""
        if not isinstance(error, OpsMeshError) or error.code == "DELIVERY_UNKNOWN":
            return False
        durable_wait = error.code in _DURABLE_WAIT_CODES
        if not durable_wait and (
            not error.retryable
            or error.code.startswith("CONVERSATION_RUN_")
            or error.code == "IDEMPOTENCY_CONFLICT"
        ):
            return False
        saved = await self.queue.get_value(key)
        transient_attempts = saved.get("transient_attempts", 0) if saved else 0
        if type(transient_attempts) is not int:
            transient_attempts = 0
        previous = (
            saved.get("attempts", 0)
            if durable_wait and saved and saved.get("error_code") == error.code
            else 0
            if durable_wait
            else transient_attempts
        )
        attempts = min(30, previous + 1) if type(previous) is int else 1
        if not durable_wait and attempts >= _TRANSIENT_RETRY_LIMIT:
            return False
        maximum = 30 if error.code.startswith("CONVERSATION_RUN_") else 300
        delay = min(maximum, 2 ** min(attempts, 9))
        if error.info.retry_after_seconds is not None:
            delay = min(maximum, max(delay, error.info.retry_after_seconds))
        await self.queue.record_error(batch_id, error.code)
        await self.queue.set_value(
            key,
            {
                "attempts": attempts,
                "transient_attempts": transient_attempts if durable_wait else attempts,
                "error_code": error.code,
                "next_attempt_at": (datetime.now(UTC) + timedelta(seconds=delay)).isoformat(),
            },
        )
        return True

    async def activity_tick(self, group: ConversationBinding) -> None:
        async with self._group_locks[group.key]:
            await self.activity.tick(group)

    async def capture_tick(self, group: ConversationBinding) -> None:
        if group.knowledge_collection is None and group.memory_namespace_id is None:
            return
        # Advance over deferred records so a busy/failed page cannot starve later work.
        page = await self.queue.list_values(
            group.capture_prefix, limit=128, after=self._capture_cursors.get(group.key)
        )
        self._capture_cursors[group.key] = page[-1][0] if len(page) == 128 else None
        for key, capture in page:
            now = datetime.now(UTC)
            deadline = capture.get("next_attempt_at")
            try:
                if isinstance(deadline, str) and datetime.fromisoformat(deadline) > now:
                    continue
                batch = await self.queue.get_batch(str(capture["batch_id"]))
                if (batch.bot_id, batch.channel_id) != (group.bot_id, group.channel_id):
                    raise OpsMeshError("CONVERSATION_STATE_INVALID", "Capture binding changed")
                if batch.state != "replied" or not batch.delivery_id:
                    continue
                await self.platform.capture(
                    group, batch, str(capture["answer"]), str(capture["run_id"])
                )
                await self.queue.delete_value(key)
            except Exception as error:
                previous_attempts = capture.get("attempts", 0)
                attempts = min(30, previous_attempts + 1) if type(previous_attempts) is int else 1
                saved = {**capture, "attempts": attempts, "error_code": _error_code(error)}
                if isinstance(error, (KeyError, ValueError, TypeError)) or (
                    isinstance(error, OpsMeshError) and not error.info.retryable
                ):
                    failed_key = key.replace(
                        "conversation-capture:", "conversation-capture-failed:", 1
                    )
                    await self.queue.set_value(failed_key, saved)
                    await self.queue.delete_value(key)
                else:
                    delay = min(3600, 30 * 2 ** min(attempts - 1, 7))
                    saved["next_attempt_at"] = (now + timedelta(seconds=delay)).isoformat()
                    await self.queue.set_value(key, saved)
                logger.warning("conversation.capture_failed code=%s", _error_code(error))

    async def maintenance_tick(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.retention_days)
        # Pending captures retain their immutable approved source transcript.
        for prefix in ("conversation-capture:", "conversation-capture-failed:"):
            after = None
            while True:
                page = await self.queue.list_values(prefix, limit=128, after=after)
                for _key, capture in page:
                    try:
                        batch = await self.queue.get_batch(str(capture["batch_id"]))
                    except KeyError:
                        continue
                    cutoff = min(cutoff, batch.created_at)
                if len(page) < 128:
                    break
                after = page[-1][0]
        await self.queue.purge_before(cutoff)
        await ConversationMedia(self.settings, self.queue, self.platform).purge(cutoff)
