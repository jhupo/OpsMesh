"""Human confirmation of uncertain effects; never dispatch or retry a Team operation."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.auth.service import AuthenticationService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from opsmesh.identity.authorization.service import WorkspaceAuthorizationService
from opsmesh.shared.security.redaction import redact_sensitive_payload, redact_sensitive_text
from opsmesh.teams.execution.models import TeamOrchestrationRequest
from opsmesh.teams.sessions.schemas import AgentTeamOrchestrationReconcileRequest


class TeamOrchestrationRequestNotFoundError(ValueError):
    pass


class TeamReconciliationConflictError(ValueError):
    pass


def require_reconciled_team(session: Session, workspace_id: UUID, team_id: UUID) -> None:
    unknown = session.scalar(
        select(TeamOrchestrationRequest.id)
        .where(
            TeamOrchestrationRequest.workspace_id == workspace_id,
            TeamOrchestrationRequest.team_id == team_id,
            TeamOrchestrationRequest.status == "unknown",
        )
        .limit(1)
    )
    if unknown is not None:
        raise TeamReconciliationConflictError(
            "Team has an unknown operation requiring human review"
        )


class TeamReconciliationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def reconcile(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        request_id: UUID,
        user: AuthenticatedUser,
        decision: AgentTeamOrchestrationReconcileRequest,
    ) -> TeamOrchestrationRequest:
        live_user = AuthenticationService(self._session).refresh_authenticated_user(user)
        WorkspaceAuthorizationService(self._session).require_workspace(
            user_id=live_user.user_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.OPERATE,
            authenticated_user=live_user,
        )
        ResourceAuthorizationService(self._session, live_user).require(
            workspace_id, ResourceKind.TEAM, team_id, ResourceAction.CONTROL
        )
        # Share the execution serialization key. A timed-out Activity may still
        # be finishing its current transaction; humans cannot settle it concurrently.
        acquired = self._session.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{workspace_id}:team:{team_id}"},
        )
        if not acquired:
            raise TeamReconciliationConflictError("Team operation is still executing")
        command = self._session.scalar(
            select(TeamOrchestrationRequest)
            .where(
                TeamOrchestrationRequest.workspace_id == workspace_id,
                TeamOrchestrationRequest.team_id == team_id,
                TeamOrchestrationRequest.id == request_id,
            )
            .with_for_update()
        )
        if command is None:
            raise TeamOrchestrationRequestNotFoundError("Team request not found")
        if command.status != "unknown":
            raise TeamReconciliationConflictError("Only an unknown Team request can be reconciled")
        confirmed_at = datetime.now(UTC)
        confirmation = {
            "reviewer_user_id": str(live_user.user_id),
            "confirmed_at": confirmed_at.isoformat(),
            "status": decision.status,
            "reason": redact_sensitive_text(decision.reason),
            "evidence": redact_sensitive_payload(decision.evidence),
            "original_error_code": command.last_error_code,
        }
        command.result = {
            "observed_result": redact_sensitive_payload(decision.observed_result),
            "reconciliation": confirmation,
        }
        command.status = decision.status
        command.completed_at = confirmed_at
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=live_user.user_id,
            action="team.orchestration.reconciled",
            target_type="team_orchestration_request",
            target_id=request_id,
            metadata={"team_id": str(team_id), **confirmation},
        )
        self._session.flush()
        return command
