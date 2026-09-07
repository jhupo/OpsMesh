from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.api.schemas.auth import (
    CurrentUserResponse,
    CurrentUserUpdateRequest,
    PasswordChangeRequest,
    UserAPITokenCreateRequest,
    UserAPITokenCreateResponse,
    UserAPITokenResponse,
    UserAPITokenRevokeAllResponse,
    UserAPITokenRotateRequest,
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
from backend.app.security.service import SecurityAuditService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=CurrentUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_user(
    request: UserRegisterRequest,
    http_request: Request,
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
    _record_auth_event(
        session,
        http_request,
        action="identity.user_registered",
        reason="User registration completed",
        user_id=user.id,
    )
    return CurrentUserResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
    )


@router.post("/login", response_model=UserAPITokenCreateResponse)
async def login_user(
    request: UserLoginRequest,
    http_request: Request,
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
        _record_auth_event(
            session,
            http_request,
            action="auth.password_login_rejected",
            reason=exc.message,
            outcome="denied",
            severity="warning",
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc
    _record_auth_event(
        session,
        http_request,
        action="auth.password_login_succeeded",
        reason="Password login completed",
        user_id=created.record.user_id,
        metadata={"token_id": str(created.record.id)},
    )
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


@router.patch("/me", response_model=CurrentUserResponse)
async def update_current_user_profile(
    request: CurrentUserUpdateRequest,
    http_request: Request,
    current_user: AuthenticatedUser = Depends(
        account_action_dependency(AccountAction.PROFILE_WRITE)
    ),
    session: Session = Depends(get_db_session),
) -> CurrentUserResponse:
    user = AuthorizationService(session).update_profile(
        user_id=current_user.user_id,
        display_name=request.display_name,
    )
    _record_auth_event(
        session,
        http_request,
        action="identity.profile_updated",
        reason="User profile updated",
        user_id=user.id,
    )
    return CurrentUserResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
    )


@router.put("/password", response_model=CurrentUserResponse)
async def change_current_user_password(
    request: PasswordChangeRequest,
    http_request: Request,
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
        _record_auth_event(
            session,
            http_request,
            action="identity.password_change_rejected",
            reason=exc.message,
            outcome="denied",
            severity="warning",
            user_id=current_user.user_id,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc
    _record_auth_event(
        session,
        http_request,
        action="identity.password_changed",
        reason="Password changed and active tokens revoked",
        user_id=user.id,
    )
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
    http_request: Request,
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
    _record_auth_event(
        session,
        http_request,
        action="auth.user_token_created",
        reason="User API token created",
        user_id=current_user.user_id,
        metadata={"token_id": str(created.record.id), "fingerprint": created.record.fingerprint},
    )
    payload = UserAPITokenResponse.model_validate(created.record).model_dump()
    return UserAPITokenCreateResponse(**payload, token=created.token)


@router.delete("/tokens", response_model=UserAPITokenRevokeAllResponse)
async def revoke_current_user_tokens(
    request: Request,
    current_user: AuthenticatedUser = Depends(
        account_action_dependency(AccountAction.TOKENS_MANAGE)
    ),
    session: Session = Depends(get_db_session),
) -> UserAPITokenRevokeAllResponse:
    tokens = AuthorizationService(session).revoke_all_user_api_tokens(
        user_id=current_user.user_id,
    )
    _record_auth_event(
        session,
        request,
        action="auth.user_tokens_revoked",
        reason="All active user API tokens revoked",
        user_id=current_user.user_id,
        metadata={"revoked_count": len(tokens)},
    )
    return UserAPITokenRevokeAllResponse(revoked=len(tokens))


@router.delete("/tokens/{token_id}", response_model=UserAPITokenResponse)
async def revoke_current_user_token(
    token_id: UUID,
    request: Request,
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
    _record_auth_event(
        session,
        request,
        action="auth.user_token_revoked",
        reason="User API token revoked",
        user_id=current_user.user_id,
        metadata={"token_id": str(token.id), "fingerprint": token.fingerprint},
    )
    return UserAPITokenResponse.model_validate(token)


@router.post("/tokens/{token_id}/rotate", response_model=UserAPITokenCreateResponse)
async def rotate_current_user_token(
    token_id: UUID,
    request: UserAPITokenRotateRequest,
    http_request: Request,
    current_user: AuthenticatedUser = Depends(
        account_action_dependency(AccountAction.TOKENS_MANAGE)
    ),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> UserAPITokenCreateResponse:
    try:
        created = AuthorizationService(session).rotate_user_api_token(
            user_id=current_user.user_id,
            token_id=token_id,
            settings=settings,
            actor=current_user,
            name=request.name,
            expires_at=request.expires_at,
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.message) from exc
    if created is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active token not found")
    _record_auth_event(
        session,
        http_request,
        action="auth.user_token_rotated",
        reason="User API token rotated",
        user_id=current_user.user_id,
        metadata={
            "replaced_token_id": str(token_id),
            "token_id": str(created.record.id),
            "fingerprint": created.record.fingerprint,
        },
    )
    payload = UserAPITokenResponse.model_validate(created.record).model_dump()
    return UserAPITokenCreateResponse(**payload, token=created.token)


def _record_auth_event(
    session: Session,
    request: Request,
    *,
    action: str,
    reason: str,
    outcome: str = "allowed",
    severity: str = "info",
    user_id: UUID | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    SecurityAuditService(session).record_request_event(
        request=request,
        action=action,
        outcome=outcome,
        severity=severity,
        reason=reason,
        user_id=user_id,
        metadata=metadata,
    )
    session.commit()
