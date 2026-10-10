from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.orm import Session

from backend.app.identity.auth.dependencies import workspace_dependency
from backend.app.identity.authorization.context import WorkspaceContext
from backend.app.identity.authorization.permissions import WorkspaceAction
from backend.app.orchestration.conversations.models import ConversationTurn
from backend.app.orchestration.conversations.schemas import (
    ConversationCreate,
    ConversationResponse,
    EventResponse,
    ExecutionResponse,
    MessageCreate,
    TurnResponse,
)
from backend.app.orchestration.conversations.service import ConversationService
from backend.app.shared.db.session import get_db_session
from backend.app.shared.http.pagination import PageResponse, pagination_params
from backend.app.shared.pagination import PageParams

router = APIRouter(prefix="/workspaces/{workspace_id}/conversations", tags=["conversations"])


def _turn_response(
    service: ConversationService, context: WorkspaceContext, turn: ConversationTurn
) -> TurnResponse:
    errors = service.turn_errors(context, turn.conversation_id, [turn])
    return TurnResponse.model_validate(turn).model_copy(update={"error": errors.get(turn.id)})


@router.post("/{conversation_id}/turns/{turn_id}/retry", response_model=TurnResponse)
def retry_turn(
    conversation_id: UUID,
    turn_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> TurnResponse:
    service = ConversationService(session)
    return _turn_response(service, context, service.retry(context, conversation_id, turn_id))


@router.get("/{conversation_id}/events", response_model=list[EventResponse])
def list_events(
    conversation_id: UUID,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> list[EventResponse]:
    return [
        EventResponse.model_validate(item)
        for item in ConversationService(session).events(
            context,
            conversation_id,
            after_id,
            limit,
        )
    ]


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation(
    request: ConversationCreate,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> ConversationResponse:
    return ConversationResponse.model_validate(
        ConversationService(session).create(context, request)
    )


@router.get("", response_model=PageResponse[ConversationResponse])
def list_conversations(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[ConversationResponse]:
    items, total = ConversationService(session).list_conversations(context, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> ConversationResponse:
    return ConversationResponse.model_validate(
        ConversationService(session).get(context, conversation_id)
    )


@router.post(
    "/{conversation_id}/messages", response_model=TurnResponse, status_code=status.HTTP_202_ACCEPTED
)
def send_message(
    conversation_id: UUID,
    request: MessageCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> TurnResponse:
    service = ConversationService(session)
    return _turn_response(
        service,
        context,
        service.send(
            context,
            conversation_id,
            request.body,
            idempotency_key,
        ),
    )


@router.get("/{conversation_id}/messages", response_model=PageResponse[TurnResponse])
def list_messages(
    conversation_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[TurnResponse]:
    service = ConversationService(session)
    items, total = service.turns(context, conversation_id, page)
    errors = service.turn_errors(context, conversation_id, items)
    responses = [
        TurnResponse.model_validate(turn).model_copy(update={"error": errors.get(turn.id)})
        for turn in items
    ]
    return PageResponse(items=responses, total=total, limit=page.limit, offset=page.offset)


@router.get("/{conversation_id}/executions", response_model=PageResponse[ExecutionResponse])
def list_executions(
    conversation_id: UUID,
    turn_id: UUID | None = Query(default=None),
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[ExecutionResponse]:
    items, total = ConversationService(session).executions(context, conversation_id, page, turn_id)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/{conversation_id}/turns/{turn_id}/cancel", response_model=TurnResponse)
def cancel_turn(
    conversation_id: UUID,
    turn_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> TurnResponse:
    service = ConversationService(session)
    return _turn_response(service, context, service.cancel(context, conversation_id, turn_id))
