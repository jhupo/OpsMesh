"""Review delegation and approval inbox operate through the same protected facade."""

import asyncio
import time

from opsmesh.application.approval_notifications import ApprovalNotificationCoordinator

from .conftest import published_agent, submit_run


async def test_approval_queue_delegation_reason_and_owner_cannot_reassign(http_platform):
    app, client, headers, principals = http_platform
    reference = await published_agent(client, headers["alice"])
    run, _ = await submit_run(client, headers["alice"], reference, text="approval")
    waiting = await app.services.runtime.execute(run["id"])
    policy_body = {
        "expected_revision": waiting.revision,
        "policy": {
            "allow_owner": False,
            "approver_ids": [principals["bob"]],
            "require_reason": True,
        },
    }
    path = f"/api/v1/runs/{run['id']}/approval-policy"
    assert (await client.put(path, json=policy_body, headers=headers["alice"])).status_code == 403
    changed = await client.put(path, json=policy_body, headers=headers["admin"])
    assert changed.status_code == 200, changed.text
    queue = await client.get("/api/v1/approvals", headers=headers["bob"])
    assert queue.status_code == 200, queue.text
    assert queue.json()[0]["run_id"] == run["id"]
    assert "expires_at" in queue.json()[0]
    assert (await client.get("/api/v1/approvals", headers=headers["alice"])).json() == []
    decision = {"expected_revision": changed.json()["revision"], "approved": True}
    path = f"/api/v1/runs/{run['id']}/approvals/a1"
    assert (await client.post(path, json=decision, headers=headers["alice"])).status_code == 403
    assert (await client.post(path, json=decision, headers=headers["bob"])).status_code == 400
    response = await client.post(
        path, json={**decision, "reason": "Reviewed and approved"}, headers=headers["bob"]
    )
    assert response.status_code == 200, response.text
    assert response.json()["approval_reviews"]["a1"]["actor_id"] == principals["bob"]


async def test_approval_reminders_and_expiry_are_idempotent_across_coordinators(http_platform):
    app, client, headers, principals = http_platform
    reference = await published_agent(client, headers["alice"])
    run, _ = await submit_run(client, headers["alice"], reference, text="approval")
    await app.services.runtime.execute(run["id"])
    row = await app.services.runtime.runs.get(run["id"])
    await app.services.runtime.runs.update(
        run["id"],
        {
            **row.data,
            "approval_policy": {
                "allow_owner": False,
                "approver_ids": [principals["bob"]],
                "timeout_seconds": 10,
            },
            "approval_wait_started_at": time.time() - 6,
        },
        row.revision,
    )
    first = ApprovalNotificationCoordinator(
        app.services.runtime, app.services.triggers, app.services.notifications
    )
    second = ApprovalNotificationCoordinator(
        app.services.runtime, app.services.triggers, app.services.notifications
    )
    await asyncio.gather(first.tick(), second.tick())
    await first.tick()
    for name in ("alice", "bob"):
        records = await app.services.notifications.list_records(actor_id=principals[name])
        assert len(records) == 1 and records[0].event_type == "runtime.approval.reminder"
    row = await app.services.runtime.runs.get(run["id"])
    await app.services.runtime.runs.update(
        run["id"], {**row.data, "approval_wait_started_at": time.time() - 11}, row.revision
    )
    await asyncio.gather(first.tick(), second.tick())
    blocked = await app.services.runtime.get_run(run["id"])
    assert blocked.status == "blocked"
    assert (await app.services.sessions.get(blocked.session_id)).active_run_id is None
    facts = await app.services.database.pending_events()
    expired = [fact for fact in facts if fact["type"] == "runtime.run.approval_expired"]
    assert len(expired) == 1
    await asyncio.gather(app.outbox.deliver(expired[0]), app.outbox.deliver(expired[0]))
    records = await app.services.notifications.list_records(actor_id=principals["alice"])
    expiry_records = [item for item in records if item.event_type == "runtime.run.approval_expired"]
    assert len(expiry_records) == 1 and "blocked" in expiry_records[0].content
