"""Own one message request through dispatch, encoding and HTTP completion."""

import logging
from contextlib import AsyncExitStack, contextmanager
from functools import partial

import anyio
from starlette.requests import ClientDisconnect

from opsmesh.dispatch import dispatch_event
from opsmesh.plugins.invocation import (
    PluginUnavailable,
    hold_plugin,
    invocation_scope,
)
from opsmesh.plugins.registry import plugin_scope
from opsmesh.utils import flatten_exception_group

from ..attachments import AttachmentCollection
from .errors import HTTPError
from .events import APIMessageEvent
from .messages import from_request
from .output import MessageOutput
from .responses import json_response, sse_event
from .streaming import MessageResponse, MessageStreamingResponse, StreamLease

logger = logging.getLogger(__name__)


class MessageExchange:
    """Separate handler dependencies from admission held by actual writes."""

    def __init__(self, channel, registration, principal):
        """Borrow the listener and authenticated route for one request."""
        self.channel = channel
        self.registration = registration
        self.principal = principal
        self.stack = AsyncExitStack()
        self.resources = StreamLease(self.stack)
        self.transport = AsyncExitStack()
        self.admitted_call = None
        self.event = None
        self.output = None

    @contextmanager
    def scope(self):
        """Reenter plugin ownership in the task that writes the response."""
        with (
            plugin_scope(self.registration.owner),
            invocation_scope(self.admitted_call),
        ):
            yield

    async def prepare(self, request) -> None:
        """Parse the request and reserve its conversation before responding."""
        self.admitted_call = hold_plugin(
            self.registration.owner, self.transport
        )
        body = await self.channel._parse_body(self.registration, request)
        files = AttachmentCollection(self.channel.settings.attachments)
        message = from_request(
            body,
            files,
            partial(
                self.channel.uploads.resolve,
                (self.registration.installation_id, self.principal.caller.id),
            ),
        )
        values = dict(
            request=request,
            payload=body,
            attachments=files,
            route_name=self.registration.name,
            installation_id=self.registration.installation_id,
            user_id=self.principal.caller.id,
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
                with anyio.fail_after(
                    self.channel.settings.request_timeout_seconds
                ):
                    if self.output.emit is not None:
                        # StreamingResponse owns the ASGI disconnect receiver.
                        outcome = await dispatch_event(
                            self.channel,
                            self.event,
                            invocation_context=self.principal.caller,
                            stack=self.stack,
                            matcher_filter=self.registration.accepts_matcher,
                            admitted_call=self.admitted_call,
                        )
                    else:
                        outcome = await self.channel._dispatch(
                            self.registration,
                            self.event,
                            self.principal,
                            self.stack,
                            self.admitted_call,
                        )
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
        return (
            ("failed", _message_error(failure)) if failure else (status, None)
        )

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


def _message_error(error: BaseException) -> tuple[int, dict[str, str]]:
    """Keep unexpected handler and cleanup details out of response bodies."""
    leaves = (
        list(flatten_exception_group(error))
        if isinstance(error, BaseExceptionGroup)
        else [error]
    )
    if len(leaves) == 1:
        error = leaves[0]
    if isinstance(error, HTTPError):
        status, message = error.status_code, error.message
    elif isinstance(error, PluginUnavailable):
        status, message = 503, "route is not ready"
    elif isinstance(error, TimeoutError):
        status, message = 504, "request timed out"
    elif isinstance(error, PermissionError):
        status, message = 403, "request is not permitted"
    else:
        logger.error(
            "API message processing failed",
            exc_info=(
                type(error),
                error,
                error.__traceback__,
            ),
        )
        status, message = 500, "internal server error"
    return status, {"code": str(status), "message": message}
