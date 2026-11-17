"""Own one message request through dispatch, encoding and HTTP completion."""

from contextlib import AsyncExitStack, nullcontext
from functools import partial

import anyio
from starlette.requests import ClientDisconnect

from opsmesh.dispatch import dispatch_event

from ..attachments import AttachmentCollection
from .errors import HTTPError, request_error
from .events import APIMessageEvent
from .messages import from_request
from .output import MessageOutput
from .requests import RequestResources, RequestScope, parse_message
from .responses import json_response, sse_event
from .streaming import MessageResponse, MessageStreamingResponse


class MessageExchange:
    """Separate handler dependencies from admission held by actual writes."""

    def __init__(self, channel, entrance):
        """Borrow the listener and authenticated entrance for one request."""
        self.channel = channel
        self.entrance = entrance
        self.caller = None
        self.stack = AsyncExitStack()
        self.resources = RequestResources(self.stack)
        self.transport = AsyncExitStack()
        self.execution = None
        self.event = None
        self.output = None

    def scope(self):
        """Enter ownership when preparation has acquired admission."""
        return self.execution.activate() if self.execution else nullcontext()

    async def prepare(self, request) -> None:
        """Parse the request and reserve its conversation before responding."""
        self.execution = RequestScope(
            self.entrance.owner,
            self.transport,
            self.channel.settings.request_timeout_seconds,
        )
        with self.scope(), self.execution.budget():
            self.caller = await self.channel.resolve_caller(request, self.stack)
            body = await parse_message(
                request, self.channel.settings.max_request_body
            )
        files = AttachmentCollection(self.channel.settings.attachments)
        message = from_request(
            body,
            files,
            partial(
                self.channel.uploads.resolve,
                (self.entrance.installation_id, self.caller.id),
            ),
        )
        values = dict(
            request=request,
            payload=body,
            attachments=files,
            route_name=self.entrance.name,
            installation_id=self.entrance.installation_id,
            user_id=self.caller.id,
            message=message,
            connection_id=self.channel.self_id,
        )
        if body.conversation_id is not None:
            values["conversation_id"] = body.conversation_id
        self.event = APIMessageEvent(**values)
        key = self.event.get_session_id()
        if key in self.channel._active_conversations:
            raise HTTPError(409, "conversation is busy")
        if not self.channel._active_conversations:
            self.channel._conversations_drained = anyio.Event()
        self.channel._active_conversations.add(key)
        self.transport.callback(self.channel._release_conversation, key)
        self.output = MessageOutput(self.channel, self.event)
        self.event.bind_output(self.output)

    async def process(self):
        """Bound both JSON and SSE handlers with the same execution budget."""
        failure = None
        status = "completed"
        with self.scope():
            try:
                with self.execution.budget():
                    if self.output.emit is not None:
                        # StreamingResponse owns the ASGI disconnect receiver.
                        outcome = await dispatch_event(
                            self.channel,
                            self.event,
                            invocation_context=self.caller,
                            stack=self.stack,
                            matcher_filter=self.entrance.accepts_matcher,
                            admitted_call=self.execution.admitted_call,
                        )
                    else:
                        outcome = await self._dispatch()
                    if outcome.errors:
                        raise outcome.errors[0]
                    if self.output.failure is not None:
                        raise self.output.failure
                    status = "waiting" if outcome.suspended else "completed"
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
        if failure:
            error = request_error(failure)
            return "failed", (
                error.status_code,
                {"code": str(error.status_code), "message": error.message},
            )
        return status, None

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
                    message = await self.event.request.receive()
                    if message["type"] == "http.disconnect":
                        disconnected = True
                        tasks.cancel_scope.cancel()
                        return

            tasks.start_soon(watch_disconnect)
            outcome = await dispatch_event(
                self.channel,
                self.event,
                invocation_context=self.caller,
                stack=self.stack,
                matcher_filter=self.entrance.accepts_matcher,
                admitted_call=self.execution.admitted_call,
            )
            tasks.cancel_scope.cancel()
        if disconnected:
            raise ClientDisconnect()
        return outcome
