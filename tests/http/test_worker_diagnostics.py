"""Run readers receive worker facts, without launch capabilities or arbitrary process output."""

import json
from datetime import UTC, datetime

from opsmesh.infrastructure.run_workers.status import WorkerProcessStatus

from .conftest import published_agent, submit_run


async def test_worker_endpoints_authorize_before_inspection_and_hide_launch_token(
    http_platform,
    monkeypatch,
):
    app, client, headers, _ = http_platform
    reference = await published_agent(client, headers["alice"])
    run, _ = await submit_run(client, headers["alice"], reference)
    inspected = []

    async def poll(run_id):
        inspected.append(run_id)
        return WorkerProcessStatus(
            run_id=run_id,
            mode="kubernetes",
            status="running",
            job_name="opsmesh-run-example",
            launch_token="internal-owner-capability",
            reason="Pending",
        )

    monkeypatch.setattr(app.launcher, "poll", poll)
    path = f"/api/v1/runs/{run['id']}/worker"
    assert (await client.get(path)).status_code == 401
    assert (await client.get(path, headers=headers["bob"])).status_code == 403
    assert not inspected
    response = await client.get(path, headers=headers["alice"])
    assert response.status_code == 200 and response.json()["mode"] == "kubernetes"
    assert response.headers["cache-control"] == "no-store"
    assert (
        "launch_token" not in response.json() and "internal-owner-capability" not in response.text
    )


async def test_worker_logs_only_project_valid_platform_diagnostics(http_platform, monkeypatch):
    app, client, headers, _ = http_platform
    reference = await published_agent(client, headers["alice"])
    run, _ = await submit_run(client, headers["alice"], reference)
    timestamp = datetime.now(UTC).isoformat()
    inspected = []

    async def logs(run_id):
        inspected.append(run_id)
        started = {"event": "worker.started", "run_id": run_id, "occurred_at": timestamp}
        finished = {
            **started,
            "event": "worker.finished",
            "status": "completed",
            "message": "arbitrary-secret-provider-output",
            "api_key": "private-api-key",
        }
        return "\n".join(
            [
                "stdout printed private-db-password",
                json.dumps(started),
                f"{timestamp} " + json.dumps(finished),
                json.dumps({**started, "run_id": "other-run"}),
                json.dumps({**started, "event": "model.raw", "response": "private-model-content"}),
                json.dumps({**started, "event": "worker.error", "code": "MODEL_UNAVAILABLE"}),
            ]
        )

    monkeypatch.setattr(app.launcher, "logs", logs)
    path = f"/api/v1/runs/{run['id']}/worker/logs"
    assert (await client.get(path, headers=headers["bob"])).status_code == 403
    assert not inspected
    response = await client.get(path, headers=headers["alice"])
    assert response.status_code == 200, response.text
    result = response.json()
    assert [entry["event"] for entry in result["entries"]] == [
        "worker.started",
        "worker.finished",
        "worker.error",
    ]
    assert result["omitted_lines"] == 3
    assert "private" not in response.text and "arbitrary-secret" not in response.text
    assert result["entries"][-1]["code"] == "MODEL_UNAVAILABLE"
    assert (
        await client.get(path + "?limit_bytes=1000001", headers=headers["alice"])
    ).status_code == 422


async def test_worker_log_limit_is_applied_before_projecting_entries(http_platform, monkeypatch):
    app, client, headers, _ = http_platform
    reference = await published_agent(client, headers["alice"])
    run, _ = await submit_run(client, headers["alice"], reference)
    entry = json.dumps(
        {
            "event": "worker.started",
            "run_id": run["id"],
            "occurred_at": datetime.now(UTC).isoformat(),
        }
    )

    async def logs(run_id):
        return (entry + "\n") * 10000

    monkeypatch.setattr(app.launcher, "logs", logs)
    response = await client.get(
        f"/api/v1/runs/{run['id']}/worker/logs?limit_bytes=1024",
        headers=headers["alice"],
    )
    assert response.status_code == 200
    assert response.json()["truncated"]
    assert len(response.json()["entries"]) < 10
