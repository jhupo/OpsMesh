"""One authenticated account and its bounded callback workers."""
import asyncio
import logging
from opsmesh.adapters import Gateway as BaseGateway
from opsmesh.message import handle_event
from .dedupe import EventDeduplicator
from .protocol.client import DingTalkClient
from .protocol.stream import DingTalkStream

class Gateway(BaseGateway):
    send_types = frozenset({"text", "markdown", "image", "file", "audio", "video"})

    def __init__(self, adapter, settings, *, owner_plugin_id: str | None = None):
        super().__init__(
            adapter, "dingtalk:" + settings.client_id, owner_plugin_id=owner_plugin_id
        )
        self.settings = settings
        self.queue = asyncio.Queue(settings.max_pending_events)
        self.dedupe = EventDeduplicator(ttl=settings.dedupe_ttl, capacity=settings.dedupe_capacity)
        self.tasks = []
        self.client = None
        self.stream = None

    @property
    def ready(self):
        return self.id in self.adapter.gateways

    async def open(self, secret):
        self.client = DingTalkClient(self.settings, secret)
        self.stream = DingTalkStream(self, secret)
        self.tasks = [asyncio.create_task(self.worker()) for _ in range(self.settings.concurrency)]
        self.tasks.append(asyncio.create_task(self.stream.run()))

    async def close(self):
        if self.stream:
            self.stream.stop()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        self.inbound_task_disconnected()
        if self.client:
            await self.client.close()
            self.client = None

    def inbound_task_connected(self):
        if not self.ready:
            self.adapter.connect(self)

    def inbound_task_disconnected(self):
        if self.ready:
            self.adapter.disconnect(self)

    async def accept(self, event, *, delivery_id):
        if self.dedupe.seen(delivery_id):
            return "duplicate"
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dedupe.forget(delivery_id)
            return "busy"
        return "ok"

    async def worker(self):
        while True:
            event = await self.queue.get()
            try:
                await handle_event(self, event)
            except Exception:
                logging.getLogger(__name__).exception("Failed to dispatch DingTalk event")
            finally:
                self.queue.task_done()

    async def send(self, event, message, **kwargs):
        return await self.call_api("send_message", event=event, message=message, **kwargs)
