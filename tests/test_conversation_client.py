import argparse
import asyncio
import json

import httpx
import pytest

from scripts.conversation_client import run


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_client_follows_tools_and_terminal_result_without_replaying(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: str
) -> None:
    monkeypatch.setenv("OPSMESH_TOKEN", "client-test-token")
    monkeypatch.setenv("OPSMESH_WORKSPACE_ID", "workspace")
    monkeypatch.setenv("OPSMESH_URL", "http://test")
    posts: list[str] = []
    turn = {
        "id": "turn",
        "sequence": 1,
        "status": "queued",
        "error_code": None,
        "error": None,
        "reply": None,
    }
    polls = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        if request.method == "POST":
            posts.append(request.url.path)
            return httpx.Response(202, json=turn)
        if request.url.path.endswith("/executions"):
            assert request.url.params["turn_id"] == "turn"
            assert int(request.url.params["limit"]) <= 100
            return httpx.Response(
                200, json={"items": [{"turn_id": "turn", "task_id": "task", "purpose": "manager"}]}
            )
        if request.url.path.endswith("/events/stream"):
            payload = {
                "event_type": "run.live",
                "payload": {
                    "kind": "tool.completed",
                    "data": {"tool_name": "search_workspace_memory"},
                },
            }
            return httpx.Response(
                200, text="event: task.event\ndata: " + json.dumps(payload) + "\n\n"
            )
        polls += 1
        await asyncio.sleep(0)
        result = {**turn, "status": "running" if polls == 1 else status}
        if polls > 1:
            result.update(
                reply="Answer" if status == "completed" else None,
                error_code="provider_request_failed" if status == "failed" else None,
                error={"code": "provider_request_failed", "message": "Bad Request"}
                if status == "failed"
                else None,
            )
        return httpx.Response(200, json={"items": [result]})

    native_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: native_client(**kwargs, transport=httpx.MockTransport(handle)),
    )
    code = asyncio.run(
        run(
            argparse.Namespace(
                conversation="conversation",
                agent=None,
                request_key="one-delivery",
                message="Question",
                timeout=5,
            )
        )
    )
    output = capsys.readouterr().out
    assert len(posts) == 1
    assert "[tool.completed] search_workspace_memory" in output
    assert "status=running" in output
    assert code == (0 if status == "completed" else 1)
    assert ("Answer" if status == "completed" else "Bad Request") in output
