from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.app.core.common.trace_context import TraceContext, trace_context
from backend.app.domains.orchestration.runs.models import AgentRun, RunEvent
from backend.app.observability.audit_models import AuditEvent
from backend.app.observability.cost_models import ModelUsageRecord
from backend.app.runtime.workers.contracts import JobPayload, JobType
from backend.app.runtime.workers.execution.runner import (
    WorkerRunner,
    WorkerRunnerConfig,
)
from backend.tests.test_worker_runner import (
    DeterministicAgentRunner,
    _queue,
    _seed_run,
    _session_factory,
)


@pytest.fixture(autouse=True)
def approve_reviews(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.domains.workspace.reviews.model_request import ModelRequestReview
    from backend.app.domains.workspace.reviews.models import ResourceReview
    from backend.app.domains.workspace.reviews.service import ResourcePolicyReviewBuilder

    monkeypatch.setattr(
        ResourcePolicyReviewBuilder,
        "review_tool_execution",
        lambda self, **kwargs: ResourceReview(
            required=False,
            risk_level="low",
            reasons=["llm_review.approved"],
            signals={"reviewer": "llm", "verdict": "approve"},
        ),
    )
    monkeypatch.setattr(
        "backend.app.domains.workspace.reviews.model_request.ModelRequestReviewService.review_request",
        lambda self, **kwargs: ModelRequestReview(
            required=False,
            risk_level="low",
            reasons=["model_request.approved"],
            signals={"reviewer": "llm", "verdict": "approve"},
        ),
    )


def test_agent_run_operations_evidence_shares_trace_and_run_identity() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, user_id = _seed_run(session_factory, slug="operations-evidence")
    parent_trace = TraceContext(
        trace_id="0123456789abcdef0123456789abcdef",
        span_id="abcdef0123456789",
    )
    with trace_context(parent_trace):
        assert queue.enqueue(
            JobPayload(
                workspace_id=workspace_id,
                job_type=JobType.AGENT_RUN,
                resource_id=run_id,
                requested_by_user_id=user_id,
                idempotency_key=f"agent.run:{workspace_id}:{run_id}",
            )
        ) is True

    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="operations-evidence-worker"),
        agent_runner=DeterministicAgentRunner(),
    )
    assert runner.run_once() is True

    with session_factory() as session:
        run = session.get(AgentRun, run_id)
        assert run is not None
        events = session.scalars(
            select(RunEvent)
            .where(RunEvent.workspace_id == workspace_id, RunEvent.agent_run_id == run_id)
            .order_by(RunEvent.sequence)
        ).all()
        usage = session.scalar(
            select(ModelUsageRecord).where(
                ModelUsageRecord.workspace_id == workspace_id,
                ModelUsageRecord.agent_run_id == run_id,
            )
        )
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "model_provider.used",
                AuditEvent.target_id == str(run_id),
            )
        )

    assert run.status == "completed"
    assert events
    assert {event.event_metadata["trace_id"] for event in events} == {parent_trace.trace_id}
    assert usage is not None
    assert usage.trace_id == parent_trace.trace_id
    assert audit is not None
    assert audit.agent_run_id == run_id
    assert audit.audit_metadata["trace_id"] == parent_trace.trace_id
    assert audit.audit_metadata["task_id"] == str(run.task_id)
