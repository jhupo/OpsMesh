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

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request.url.path)
            return httpx.Response(202, json=turn)
        if request.url.path.endswith("/events/stream"):
            assert request.url.path.endswith("/turns/turn/events/stream")
            payload = {
                "event_type": "run.live",
                "payload": {
                    "kind": "tool.completed",
                    "data": {"tool_name": "search_workspace_memory"},
                },
            }
            result = {**turn, "status": status}
            result.update(
                reply="Answer" if status == "completed" else None,
                error_code="provider_request_failed" if status == "failed" else None,
                error={"code": "provider_request_failed", "message": "Bad Request"}
                if status == "failed"
                else None,
            )
            return httpx.Response(
                200,
                text=(
                    "event: turn.snapshot\ndata: "
                    + json.dumps({"turn": {**turn, "status": "running"}, "task_id": "task"})
                    + "\n\n"
                    + "event: task.event\ndata: "
                    + json.dumps(payload)
                    + "\n\n"
                    + "event: stream.completed\ndata: "
                    + json.dumps({"turn": result, "task_id": "task"})
                    + "\n\n"
                ),
            )
        raise AssertionError("Client must not poll messages or executions")

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


def test_stream_reconnect_keeps_last_complete_frame_cursor(monkeypatch, capsys) -> None:
    from scripts.conversation_client import follow_turn

    class Interrupted(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'id: 10-0\nevent: task.event\ndata: {"event_type":"unrelated"}\n\n'
            yield b"id: 11-0\nevent: task.event\n"
            raise httpx.ReadError("interrupted before frame data")

    requests = []

    async def handle(request):
        requests.append(request.headers["Last-Event-ID"])
        if len(requests) == 1:
            return httpx.Response(200, stream=Interrupted())
        return httpx.Response(
            200,
            text='event: stream.completed\ndata: '
            '{"turn":{"status":"completed","reply":"done"}}\n\n',
        )

    async def exercise():
        async with httpx.AsyncClient(
            base_url="http://test", transport=httpx.MockTransport(handle)
        ) as client:
            assert await follow_turn(client, "/stream") == 0

    asyncio.run(exercise())
    assert requests == ["0-0", "10-0"]
    assert "done" in capsys.readouterr().out
