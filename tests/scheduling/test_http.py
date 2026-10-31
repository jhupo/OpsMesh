from datetime import UTC, datetime, timedelta

from ..http.conftest import http_platform as http_platform
from ..http.conftest import published_agent


async def test_schedule_http_crud_preview_manual_run_and_history(http_platform):
    app, client, headers, principals = http_platform
    reference = await published_agent(client, headers["admin"])
    body = {
        "id": "report",
        "principal_id": principals["admin"],
        "agent_revision": reference,
        "next_run_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "cron_expression": "0 9 * * 1-5",
        "timezone": "Asia/Shanghai",
        "input": "hello",
    }
    created = await client.post("/api/v1/schedules", headers=headers["admin"], json=body)
    assert created.status_code == 201, created.text
    assert created.json()["revision"] == 1
    preview = await client.post(
        "/api/v1/schedules/report/preview",
        headers=headers["admin"],
        json={"expected_revision": 1, "count": 3},
    )
    assert preview.status_code == 200 and len(preview.json()["times"]) == 3
    assert preview.json()["dst_policy"] == "skip_nonexistent_repeat_ambiguous"
    changed = await client.put(
        "/api/v1/schedules/report",
        headers=headers["admin"],
        json={"expected_revision": 1, "schedule": {**created.json(), "input": "updated"}},
    )
    assert changed.status_code == 200 and changed.json()["revision"] == 2
    stale = await client.post(
        "/api/v1/schedules/report/run-now",
        headers=headers["admin"],
        json={"expected_revision": 1, "idempotency_key": "one"},
    )
    assert stale.status_code == 409
    fired = await client.post(
        "/api/v1/schedules/report/run-now",
        headers=headers["admin"],
        json={"expected_revision": 2, "idempotency_key": "one"},
    )
    assert fired.status_code == 200, fired.text
    assert fired.json()["status"] == "submitted" and fired.json()["run_id"]
    run_id = fired.json()["run_id"]
    assert (await app.services.runtime.execute(run_id)).status == "completed"
    await app.services.scheduler.reconcile()
    history = await client.get("/api/v1/schedules/report/occurrences", headers=headers["admin"])
    assert history.json()[0]["status"] == "completed"
    denied = await client.get("/api/v1/schedules/report", headers=headers["alice"])
    assert denied.status_code == 403
    deleted = await client.delete(
        "/api/v1/schedules/report?expected_revision=2", headers=headers["admin"]
    )
    assert deleted.status_code == 204
    assert (
        await client.get("/api/v1/schedules/report", headers=headers["admin"])
    ).status_code == 404


async def test_invalid_schedule_does_not_become_http_500(http_platform):
    _, client, headers, principals = http_platform
    response = await client.post(
        "/api/v1/schedules",
        headers=headers["admin"],
        json={
            "id": "invalid",
            "principal_id": principals["admin"],
            "cron_expression": "0 9 * * MON",
        },
    )
    assert response.status_code == 400, response.text
