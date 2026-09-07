from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.schemas.auth import (
    CurrentUserResponse,
    PasswordChangeRequest,
    UserAPITokenCreateRequest,
    UserAPITokenCreateResponse,
    UserAPITokenResponse,
    UserAPITokenRevokeAllResponse,
    UserLoginRequest,
    UserRegisterRequest,
)
from backend.app.auth.context import AuthenticatedUser
from backend.app.auth.dependencies import account_action_dependency
from backend.app.auth.errors import AuthenticationError, PermissionDeniedError
from backend.app.auth.permissions import AccountAction
from backend.app.auth.service import AuthorizationService
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ConflictError
from backend.app.db.session import get_db_session

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=CurrentUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_user(
    request: UserRegisterRequest,
    session: Session = Depends(get_db_session),
) -> CurrentUserResponse:
    try:
        user = AuthorizationService(session).register_user(
            email=request.email,
            display_name=request.display_name,
            password=request.password,
        )
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return CurrentUserResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
    )


@router.post("/login", response_model=UserAPITokenCreateResponse)
async def login_user(
    request: UserLoginRequest,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> UserAPITokenCreateResponse:
    try:
        created = AuthorizationService(session).login_with_password(
            email=request.email,
            password=request.password,
            settings=settings,
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc
    payload = UserAPITokenResponse.model_validate(created.record).model_dump()
    return UserAPITokenCreateResponse(**payload, token=created.token)


@router.get("/me", response_model=CurrentUserResponse)
async def get_current_user_profile(
    current_user: AuthenticatedUser = Depends(
        account_action_dependency(AccountAction.PROFILE_READ)
    ),
) -> CurrentUserResponse:
    return CurrentUserResponse(
        user_id=current_user.user_id,
        email=current_user.email,
        display_name=current_user.display_name,
    )


@router.put("/password", response_model=CurrentUserResponse)
async def change_current_user_password(
    request: PasswordChangeRequest,
    current_user: AuthenticatedUser = Depends(
        account_action_dependency(AccountAction.PASSWORD_CHANGE)
    ),
    session: Session = Depends(get_db_session),
) -> CurrentUserResponse:
    try:
        user = AuthorizationService(session).change_password(
            user_id=current_user.user_id,
            current_password=request.current_password,
            new_password=request.new_password,
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc
    return CurrentUserResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
    )


@router.get("/tokens", response_model=list[UserAPITokenResponse])
async def list_current_user_tokens(
    current_user: AuthenticatedUser = Depends(
        account_action_dependency(AccountAction.TOKENS_READ)
    ),
    session: Session = Depends(get_db_session),
) -> list[UserAPITokenResponse]:
    tokens = AuthorizationService(session).list_user_api_tokens(current_user.user_id)
    return [UserAPITokenResponse.model_validate(token) for token in tokens]


@router.post(
    "/tokens",
    response_model=UserAPITokenCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_current_user_token(
    request: UserAPITokenCreateRequest,
    current_user: AuthenticatedUser = Depends(
        account_action_dependency(AccountAction.TOKENS_MANAGE)
    ),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> UserAPITokenCreateResponse:
    try:
        created = AuthorizationService(session).create_user_api_token(
            user_id=current_user.user_id,
            name=request.name,
            expires_at=request.expires_at,
            scopes=request.scopes.model_dump(mode="json") if request.scopes is not None else None,
            actor=current_user,
            settings=settings,
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.message) from exc
    payload = UserAPITokenResponse.model_validate(created.record).model_dump()
    return UserAPITokenCreateResponse(**payload, token=created.token)


@router.delete("/tokens", response_model=UserAPITokenRevokeAllResponse)
async def revoke_current_user_tokens(
    current_user: AuthenticatedUser = Depends(
        account_action_dependency(AccountAction.TOKENS_MANAGE)
    ),
    session: Session = Depends(get_db_session),
) -> UserAPITokenRevokeAllResponse:
    tokens = AuthorizationService(session).revoke_all_user_api_tokens(
        user_id=current_user.user_id,
    )
    return UserAPITokenRevokeAllResponse(revoked=len(tokens))


@router.delete("/tokens/{token_id}", response_model=UserAPITokenResponse)
async def revoke_current_user_token(
    token_id: UUID,
    current_user: AuthenticatedUser = Depends(
        account_action_dependency(AccountAction.TOKENS_MANAGE)
    ),
    session: Session = Depends(get_db_session),
) -> UserAPITokenResponse:
    token = AuthorizationService(session).revoke_user_api_token(
        user_id=current_user.user_id,
        token_id=token_id,
    )
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    return UserAPITokenResponse.model_validate(token)
