"""An accepted Run survives failures in its user-facing history projection."""

from .conftest import published_agent, submit_run


async def test_accepted_run_recovers_user_history_from_outbox(http_platform, monkeypatch):
    app, client, headers, principals = http_platform
    reference = await published_agent(client, headers["alice"])
    original = app.services.sessions.append
    failures = 1

    async def unavailable_once(*args, **kwargs):
        nonlocal failures
        if failures:
            failures -= 1
            raise OSError("temporary history storage failure")
        return await original(*args, **kwargs)

    monkeypatch.setattr(app.services.sessions, "append", unavailable_once)
    run, request = await submit_run(client, headers["alice"], reference, text="retained input")
    assert run["status"] == "queued"
    found = await app.services.runtime.find_submission(
        principals["alice"], request["idempotency_key"]
    )
    assert found.id == run["id"]
    # Deliberately complete before the deferred input projection is replayed.
    completed = await app.services.runtime.execute(run["id"])
    assert completed.status == "completed"
    fact = next(
        item
        for item in await app.services.database.pending_events(1000)
        if item["type"] == "runtime.run.queued" and item["correlation_id"] == run["id"]
    )
    await app.coordinator.deliver(fact)
    await app.coordinator.deliver(fact)
    history = await app.services.sessions.history(run["session_id"])
    assert [(item.role, item.text) for item in history] == [
        ("user", "retained input"),
        ("assistant", "hello"),
    ]
