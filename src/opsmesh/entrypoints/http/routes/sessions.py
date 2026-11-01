"""Conversation ownership and visible message history."""

from fastapi import APIRouter, Request
from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.sessions.api import ConversationMessage, ConversationSession

from ..security import CurrentUser, get_application

router = APIRouter(prefix="/sessions", tags=["sessions"])


class OpenSessionRequest(OpsMeshContract):
    agent_ref: str = Field(min_length=1)
    workspace_id: str | None = None


@router.post("", status_code=201)
async def open_session(
    body: OpenSessionRequest, request: Request, user: CurrentUser
) -> ConversationSession:
    return await get_application(request).services.conversation_management.open(
        body.agent_ref, body.workspace_id, actor_id=user.id
    )


@router.get("")
async def sessions(
    request: Request, user: CurrentUser, owner_id: str | None = None
) -> list[ConversationSession]:
    return await get_application(request).services.conversation_management.list(
        actor_id=user.id, owner_id=owner_id
    )


@router.get("/{session_id}")
async def session(session_id: str, request: Request, user: CurrentUser) -> ConversationSession:
    return await get_application(request).services.conversation_management.get(
        session_id, actor_id=user.id
    )


@router.delete("/{session_id}")
async def close_session(
    session_id: str, request: Request, user: CurrentUser
) -> ConversationSession:
    return await get_application(request).services.conversation_management.close(
        session_id, actor_id=user.id
    )


@router.get("/{session_id}/messages")
async def history(
    session_id: str, request: Request, user: CurrentUser
) -> list[ConversationMessage]:
    return await get_application(request).services.conversation_management.history(
        session_id, actor_id=user.id
    )
