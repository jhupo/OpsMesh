"""Submission acknowledgment failures must retain the already accepted Run identity."""

from datetime import UTC, datetime, timedelta

import pytest

from opsmesh.framework.events import OpsMeshEvent
from opsmesh.modules.scheduling.api import PersistentSchedule
from opsmesh.modules.triggers.api import TriggerDefinition

from ..http.conftest import http_platform as http_platform
from ..http.conftest import published_agent


@pytest.mark.parametrize("entrypoint", ["trigger", "schedule"])
async def test_run_committed_before_history_failure_is_recovered_without_resubmission(
    http_platform,
    monkeypatch,
    entrypoint,
):
    app, client, headers, principals = http_platform
    reference = await published_agent(client, headers["admin"])
    actor = principals["admin"]
    original_append = app.services.sessions.append
    failed_once = False

    async def fail_after_run_commit(*args, **kwargs):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("History store temporarily unavailable after Run commit")
        return await original_append(*args, **kwargs)

    monkeypatch.setattr(app.services.sessions, "append", fail_after_run_commit)
    if entrypoint == "trigger":
        await app.services.triggers.create(
            TriggerDefinition(
                id="acknowledgment",
                principal_id=actor,
                agent_revision=reference,
                source="monitor",
                event_type="incident.opened",
            ),
            actor_id=actor,
        )
        event = OpsMeshEvent(id="committed-event", source="monitor", type="incident.opened")
        (accepted,) = await app.services.triggers.dispatch_event(event, actor_id=actor)
        (retry,) = await app.services.triggers.dispatch_event(event, actor_id=actor)
    else:
        await app.services.scheduler.create(
            PersistentSchedule(
                id="acknowledgment",
                principal_id=actor,
                agent_revision=reference,
                next_run_at=datetime.now(UTC) + timedelta(days=1),
                interval_seconds=60,
            ),
            actor_id=actor,
        )
        accepted = await app.services.scheduler.run_now(
            "acknowledgment", 1, actor_id=actor, idempotency_key="committed-event"
        )
        retry = await app.services.scheduler.run_now(
            "acknowledgment", 1, actor_id=actor, idempotency_key="committed-event"
        )
        assert (await app.services.scheduler.get("acknowledgment", actor_id=actor)).enabled
    assert failed_once
    runs = await app.services.runtime.list_runs(principal_id=actor)
    assert len(runs) == 1
    assert accepted.status == "submitted", accepted.error
    assert accepted.run_id == retry.run_id == runs[0].id
