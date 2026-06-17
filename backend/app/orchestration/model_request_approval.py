from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunRequest
from backend.app.approvals.models import Approval
from backend.app.approvals.service import ApprovalService
from backend.app.core.config import Settings
from backend.app.orchestration.model_request_reviewing import (
    model_request_review_context,
    model_request_review_fingerprint,
    model_request_review_input,
)
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.reviews.model_request import ModelRequestReview, ModelRequestReviewService
from backend.app.runs.models import AgentRun
from backend.app.runs.service import RunStateService
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus


@dataclass(slots=True)
class ModelRequestApprovalService:
    session: Session
    settings: Settings | None
    events: RunEventRecorder

    def requires_approval(self, run: AgentRun, request: AgentRunRequest) -> bool:
        input_text = model_request_review_input(
            run,
            self.session.get(Task, run.task_id) if run.task_id is not None else None,
            request,
        )
        request_fingerprint = model_request_review_fingerprint(request, input_text)
        review = ModelRequestReviewService(self.session, self.settings).review_request(
            workspace_id=run.workspace_id,
            input_text=input_text,
            context=model_request_review_context(request),
        )
        if review.approved or self.already_approved(run, request_fingerprint):
            return False
        self.request_approval(run, request, review, request_fingerprint)
        return True

    def request_approval(
        self,
        run: AgentRun,
        request: AgentRunRequest,
        review: ModelRequestReview,
        request_fingerprint: str,
    ) -> None:
        ApprovalService(self.session).create_approval(
            workspace_id=run.workspace_id,
            task_id=run.task_id,
            agent_run_id=run.id,
            requested_by_agent_profile_id=run.agent_profile_id,
            approval_type="model.request",
            risk_level=review.risk_level,
            payload={
                "reason": "model_request_review_requires_approval",
                "model": request.model,
                "provider": request.provider,
                "model_provider_credential_id": str(request.model_provider_credential_id)
                if request.model_provider_credential_id is not None
                else None,
                "request_fingerprint": request_fingerprint,
                "review": review.approval_payload(),
            },
        )
        RunStateService().transition(run, RunStatus.WAITING_APPROVAL)
        if run.task_id is not None:
            task = self.session.get(Task, run.task_id)
            if task is not None and task.status == TaskStatus.RUNNING.value:
                TaskStateService().transition(task, TaskStatus.WAITING_APPROVAL)
        self.events.append_event(
            run,
            "approval.requested",
            "Model request requires approval",
            {
                "approval_type": "model.request",
                "risk_level": review.risk_level,
                "reasons": review.reasons,
            },
        )

    def already_approved(self, run: AgentRun, request_fingerprint: str) -> bool:
        approved = self.session.scalar(
            select(Approval.id).where(
                Approval.workspace_id == run.workspace_id,
                Approval.agent_run_id == run.id,
                Approval.approval_type == "model.request",
                Approval.status == "approved",
                Approval.payload["request_fingerprint"].as_string() == request_fingerprint,
            )
        )
        return approved is not None
