"""Durable initiating identities, shared by admission, retries and runtime calls."""

from copy import deepcopy
from dataclasses import replace
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.access.context import AuthenticatedUser
from backend.app.domains.access.errors import AuthorizationError
from backend.app.domains.access.permissions import WorkspaceAction
from backend.app.domains.access.resource_queries import resource_query_scope
from backend.app.domains.access.resources import (
    ResourceAccessDenied,
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from backend.app.domains.access.service import AuthorizationService
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.tasks.models import Task


class ExecutionIdentityService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def capture(self, workspace_id: UUID, user_id: UUID) -> dict[str, object]:
        scope = resource_query_scope(self._session)
        auth = AuthorizationService(self._session)
        if scope is not None:
            if scope.workspace_id != workspace_id or scope.user.user_id != user_id:
                raise ResourceAccessDenied()
            user = auth.refresh_authenticated_user(scope.user)
        else:
            user = auth.authenticate_user(user_id)
        auth.require_workspace(
            user_id=user.user_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.WRITE,
            authenticated_user=user,
        )
        return {
            "user_id": str(user.user_id),
            "token_id": str(user.token_id) if user.token_id is not None else None,
            "token_scopes": deepcopy(user.token_scopes),
        }

    def restore(self, workspace_id: UUID, identity: dict[str, object] | None) -> AuthenticatedUser:
        if not isinstance(identity, dict):
            raise ResourceAccessDenied()
        try:
            user_id = UUID(str(identity["user_id"]))
            token_id = UUID(str(identity["token_id"])) if identity.get("token_id") else None
            scopes = identity.get("token_scopes")
            if scopes is not None and not isinstance(scopes, dict):
                raise ResourceAccessDenied()
            captured = AuthenticatedUser(
                user_id=user_id,
                email="",
                display_name="",
                token_id=token_id,
                token_scopes=deepcopy(scopes),
            )
            auth = AuthorizationService(self._session)
            live = auth.refresh_authenticated_user(captured)
            auth.require_workspace(
                user_id=user_id,
                workspace_id=workspace_id,
                action=WorkspaceAction.WRITE,
                authenticated_user=live,
            )
            # Tokens are immutable; rotation creates a new ID. Never replace the accepted
            # ceiling with a broader credential supplied by a retry or queue message.
            if live.token_scopes != captured.token_scopes:
                raise ResourceAccessDenied()
            return replace(live, token_scopes=deepcopy(captured.token_scopes))
        except (ValueError, KeyError, TypeError, AuthorizationError) as exc:
            raise ResourceAccessDenied() from exc

    def for_task(self, workspace_id: UUID, task_id: UUID) -> AuthenticatedUser:
        identity = self._session.scalar(
            select(Task.execution_identity).where(
                Task.workspace_id == workspace_id,
                Task.id == task_id,
            )
        )
        user = self.restore(workspace_id, identity)
        ResourceAuthorizationService(self._session, user).require(
            workspace_id, ResourceKind.TASK, task_id, ResourceAction.INVOKE
        )
        return user

    def for_run(self, workspace_id: UUID, run_id: UUID) -> AuthenticatedUser:
        run = self._session.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.id == run_id,
            )
        )
        if run is None or run.task_id is None:
            raise ResourceAccessDenied()
        task = self._session.scalar(
            select(Task).where(
                Task.workspace_id == workspace_id,
                Task.id == run.task_id,
            )
        )
        if task is None:
            raise ResourceAccessDenied()
        user = self.restore(workspace_id, task.execution_identity)
        access = ResourceAuthorizationService(self._session, user)
        references = []
        for kind, identifier, action in (
            (ResourceKind.TASK, task.id, ResourceAction.INVOKE),
            (ResourceKind.TEAM, task.agent_team_id, ResourceAction.INVOKE),
            (ResourceKind.AGENT, run.agent_profile_id, ResourceAction.INVOKE),
            (ResourceKind.WORKFLOW, task.orchestration_definition_id, ResourceAction.INVOKE),
            (ResourceKind.PROJECT, task.workspace_project_id, ResourceAction.INVOKE),
            (ResourceKind.RUNTIME_SPACE, task.runtime_space_id, ResourceAction.INVOKE),
        ):
            if identifier is not None:
                references.append((kind, identifier, action))
        snapshot = run.input.get("authorization_snapshot")
        if isinstance(snapshot, dict):
            pending = list(snapshot.get("agent_tools") or [])
            while pending:
                item = pending.pop()
                if not isinstance(item, dict) or not isinstance(item.get("target"), dict):
                    raise ResourceAccessDenied()
                target = item["target"]
                try:
                    identifier = UUID(str(target["profile_id"]))
                except (KeyError, ValueError, TypeError) as exc:
                    raise ResourceAccessDenied() from exc
                references.append((ResourceKind.AGENT, identifier, ResourceAction.INVOKE))
                children = item.get("agent_tools", [])
                if not isinstance(children, list):
                    raise ResourceAccessDenied()
                pending.extend(children)
                if len(references) > 100:
                    raise ResourceAccessDenied()
        access.require_many(workspace_id, references)
        return user
