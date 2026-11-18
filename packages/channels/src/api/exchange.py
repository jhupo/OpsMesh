"""Own one message request through dispatch, encoding and HTTP completion."""

import logging
from contextlib import AsyncExitStack, nullcontext
from functools import partial
from uuid import NAMESPACE_URL, uuid4, uuid5

import anyio
from starlette.requests import ClientDisconnect

from ..attachments import AttachmentCollection
from ..context import bind_event_channel
from .codec import from_request, json_response, sse_event
from .errors import request_error
from .events import APIMessageEvent
from .output import MessageOutput
from .requests import RequestResources, RequestScope, parse_message
from .streaming import MessageResponse, MessageStreamingResponse

logger = logging.getLogger(__name__)


class MessageExchange:
    """Separate handler dependencies from admission held by actual writes."""

    def __init__(self, channel, request) -> None:
        """Borrow the connection for one HTTP request."""
        self.channel = channel
        self.request = request
        self.stack = AsyncExitStack()
        self.resources = RequestResources(self.stack)
        self.transport = AsyncExitStack()
        self.execution: RequestScope | None = None
        self.event: APIMessageEvent | None = None
        self.output: MessageOutput | None = None

    def scope(self):
        """Enter ownership when preparation has acquired admission."""
        return self.execution.activate() if self.execution else nullcontext()

    async def prepare(self, request) -> None:
        """Parse the request and reserve its conversation before responding."""
        self.execution = RequestScope(
            self.channel.owner,
            self.transport,
            self.channel.settings.request_timeout_seconds,
        )
        with self.scope(), self.execution.budget():
            body = await parse_message(
                request, self.channel.settings.max_request_body
            )
            files = AttachmentCollection(self.channel.settings.attachments)
            message = from_request(
                body,
                files,
                partial(
                    self.channel.uploads.resolve,
                    self.channel.self_id,
                ),
            )
            user = body.user.to_context(self.channel.self_id)
            conversation_id = body.conversation_id or (
                str(uuid5(NAMESPACE_URL, f"opsmesh:group:{body.group_id}"))
                if body.group_id is not None
                else str(uuid4())
            )
        self.event = APIMessageEvent(
            connection_id=self.channel.self_id,
            message_id=uuid4().hex,
            user=user,
            conversation_id=conversation_id,
            conversation_type=body.conversation_type,
            group_id=body.group_id,
            message=message,
            attachments=files,
            to_me=True,
        )
        key = self.event.get_session_id()
        self.channel.reserve_conversation(key)
        self.transport.callback(self.channel.release_conversation, key)
        self.output = MessageOutput(self.channel, self.event)
        self.event.bind_output(self.output)

    async def process(self):
        """Bound both JSON and SSE handlers with the same execution budget."""
        failure: BaseException | None = None
        multiple = False
        outcome = None
        with self.scope(), bind_event_channel(self.event, self.channel):
            try:
                with self.execution.budget():
                    if self.output.emit is not None:
                        # StreamingResponse owns the ASGI disconnect receiver.
                        outcome = await self._dispatch_event()
                    else:
                        outcome = await self._dispatch()
                    if outcome.errors:
                        multiple = len(outcome.errors) > 1
                        failure = outcome.errors[0]
                    elif self.output.failure is not None:
                        failure = self.output.failure
            except BaseException as error:
                failure = error
            finally:
                self.output.close()
                try:
                    await self.resources.close(failure)
                except BaseException as error:
                    failure = failure or error
        if isinstance(
            failure, (anyio.get_cancelled_exc_class(), ClientDisconnect)
        ):
            raise failure
        if multiple:
            for error in outcome.errors:
                logger.error(
                    "HTTP handler failed",
                    exc_info=(type(error), error, error.__traceback__),
                )
            return "failed", (500, _error_payload(500, "handlers failed"))
        if failure:
            error = request_error(failure)
            return "failed", (
                error.status_code,
                _error_payload(error.status_code, error.message),
            )
        if outcome.suspended:
            return "waiting", None
        return ("completed" if outcome.handled else "unhandled"), None

    async def close(self, error=None) -> None:
        """Release handler resources before transport admission."""
        with self.scope(), anyio.CancelScope(shield=True):
            try:
                await self.resources.close(error)
                if self.output is not None:
                    self.output.close()
                    await self.output.finish_transport(error)
            finally:
                await self.transport.aclose()

    async def produce(self, emit) -> None:
        """Emit message frames and a final status after handlers finish."""
        self.output.emit = emit
        status, failure = await self.process()
        if failure is not None:
            _, error = failure
            payload = {"conversation_id": self.event.conversation_id, **error}
            if self.output.current_message_id is not None:
                payload["message_id"] = self.output.current_message_id
            await emit(sse_event("error", payload))
        else:
            await emit(
                sse_event(
                    "done",
                    {
                        "conversation_id": self.event.conversation_id,
                        "status": status,
                    },
                )
            )

    async def respond(self, request):
        """Transfer cleanup ownership only after constructing a response."""
        try:
            await self.prepare(request)
            accepts = request.headers.get("accept", "")
            if any(
                part.split(";", 1)[0].strip() == "text/event-stream"
                for part in accepts.split(",")
            ):
                return MessageStreamingResponse(
                    self.produce,
                    capacity=self.channel.settings.stream_buffer_size,
                    on_close=self.close,
                    on_written=self.output.written,
                    execution_scope=self.scope,
                )
            status, failure = await self.process()
            code, error = failure if failure else (200, None)
            response = json_response(
                self.output.envelope(status, error), status_code=code
            )
            return MessageResponse(
                response, on_close=self.close, execution_scope=self.scope
            )
        except BaseException as error:
            await self.close(error)
            raise

    async def _dispatch(self):
        """Cancel JSON message processing when the HTTP peer disconnects."""
        disconnected = False
        outcome = None
        async with anyio.create_task_group() as tasks:

            async def watch_disconnect():
                nonlocal disconnected
                while True:
                    message = await self.request.receive()
                    if message["type"] == "http.disconnect":
                        disconnected = True
                        tasks.cancel_scope.cancel()
                        return

            tasks.start_soon(watch_disconnect)
            outcome = await self._dispatch_event()
            tasks.cancel_scope.cancel()
        if disconnected:
            raise ClientDisconnect()
        return outcome

    async def _dispatch_event(self):
        """Submit the event and retain its request resources."""
        return await self.channel.dispatch(
            self.event,
            stack=self.stack,
            admitted_call=self.execution.admitted_call,
        )


def _error_payload(status: int, message: str) -> dict:
    """Return the client-visible terminal error shape."""
    return {"code": str(status), "message": message}
