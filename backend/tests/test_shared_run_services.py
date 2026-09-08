from uuid import uuid4

import pytest

from backend.app.approvals.waiting import ApprovalWaitingService
from backend.app.runs.event_writer import RunEventWriter
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task
from backend.tests.test_product_tools import _seed_workspace, _session


def test_waiting_rejects_foreign_task_before_changing_run() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    _, foreign = _seed_workspace(session, email="foreign@example.com", slug="foreign")
    task = Task(workspace_id=foreign.id, title="Foreign", status="running")
    session.add(task)
    session.flush()
    run = AgentRun(workspace_id=workspace.id, task_id=task.id, status="running")
    session.add(run)
    session.commit()
    with pytest.raises(ValueError, match="task not found"):
        ApprovalWaitingService(session).mark_waiting(
            workspace_id=workspace.id, run_id=run.id, task_id=task.id,
        )
    assert run.status == "running"
    assert task.status == "running"


def test_event_writer_scopes_orders_and_redacts_events() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run = AgentRun(workspace_id=workspace.id)
    session.add(run)
    session.commit()
    writer = RunEventWriter(session)
    with pytest.raises(ValueError, match="not found"):
        writer.append(workspace_id=uuid4(), run_id=run.id, event_type="test", message="test")
    events = [writer.append(
        workspace_id=workspace.id, run_id=run.id, event_type="test", message="test",
        metadata={"api_key": "private-key"},
    ) for _ in range(2)]
    assert [event.sequence for event in events] == [1, 2]
    assert events[0].event_metadata["api_key"] == "[redacted]"
