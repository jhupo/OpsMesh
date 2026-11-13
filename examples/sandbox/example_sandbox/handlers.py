"""Declare a conversation message entrance and owned session operations."""

from typing import Annotated
from uuid import UUID

from pydantic import ValidationError

from opsmesh.params import Context, Depends

from . import channels
from .commands import COMMAND_MATCHERS
from .lifecycle import get_conversations
from .messages import message_handler
from .schemas import (
    ChatRequest,
    ConversationCreate,
    ConversationView,
)
from .sessions import ConversationError

channels.api.message(
    "/chat", body=ChatRequest, handlers=(*COMMAND_MATCHERS, message_handler)
)


@channels.api.post("/sessions", body=ConversationCreate)
async def create_conversation(
    event: channels.api.HTTPEvent,
    caller: Annotated[channels.UserContext, Context()],
) -> ConversationView:
    """Persist an empty conversation owned by the authenticated caller."""
    return await get_conversations().create(caller.id, event.payload.name)


@channels.api.get("/sessions")
async def list_conversations(
    event: channels.api.HTTPEvent,
    caller: Annotated[channels.UserContext, Context()],
) -> list[ConversationView]:
    """List owned conversations with bounded pagination."""
    try:
        limit = int(event.request.query_params.get("limit", "50"))
        offset = int(event.request.query_params.get("offset", "0"))
        return await get_conversations().list(
            caller.id, limit=limit, offset=offset
        )
    except ValueError:
        raise channels.api.HTTPError(422, "Invalid pagination") from None
    except ConversationError as error:
        raise channels.api.HTTPError(error.status, str(error)) from None


async def selected_id(event: channels.api.HTTPEvent) -> str:
    """Validate the public identifier before looking up ownership."""
    try:
        return str(UUID(event.request.path_params["id"]))
    except (ValueError, ValidationError):
        raise channels.api.HTTPError(422, "Invalid conversation ID") from None


@channels.api.get("/sessions/{id}")
async def get_conversation(
    session_id: Annotated[str, Depends(selected_id)],
    caller: Annotated[channels.UserContext, Context()],
) -> ConversationView:
    """Read an owned conversation's metadata."""
    try:
        return await get_conversations().get(caller.id, session_id)
    except ConversationError as error:
        raise channels.api.HTTPError(error.status, str(error)) from None


@channels.api.get("/sessions/{id}/messages")
async def get_messages(
    session_id: Annotated[str, Depends(selected_id)],
    caller: Annotated[channels.UserContext, Context()],
) -> list[dict]:
    """Read at most the latest 100 SDK history items."""
    try:
        return await get_conversations().messages(caller.id, session_id)
    except ConversationError as error:
        raise channels.api.HTTPError(error.status, str(error)) from None


@channels.api.delete("/sessions/{id}/messages")
async def clear_conversation(
    session_id: Annotated[str, Depends(selected_id)],
    caller: Annotated[channels.UserContext, Context()],
) -> None:
    """Clear history without deleting the conversation index."""
    try:
        await get_conversations().clear(caller.id, session_id)
    except ConversationError as error:
        raise channels.api.HTTPError(error.status, str(error)) from None


@channels.api.delete("/sessions/{id}")
async def delete_conversation(
    session_id: Annotated[str, Depends(selected_id)],
    caller: Annotated[channels.UserContext, Context()],
) -> None:
    """Delete history and its owned index, retrying pending deletions."""
    try:
        await get_conversations().delete(caller.id, session_id)
    except ConversationError as error:
        raise channels.api.HTTPError(error.status, str(error)) from None


@channels.api.delete("/sessions/{id}/workspace")
async def reset_workspace(
    session_id: Annotated[str, Depends(selected_id)],
    caller: Annotated[channels.UserContext, Context()],
) -> None:
    """Reset files explicitly; chat-history clearing does not remove them."""
    try:
        await get_conversations().reset_workspace(caller.id, session_id)
    except ConversationError as error:
        raise channels.api.HTTPError(error.status, str(error)) from None
