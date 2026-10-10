"""Submit an OpsMesh conversation and display its existing task event streams."""

import argparse
import asyncio
import json
import os
from uuid import uuid4

import httpx


async def follow_task(client: httpx.AsyncClient, root: str, task_id: str) -> None:
    """Consume the platform SSE contract; never replay a message or an Agent run."""
    async with client.stream(
        "GET",
        f"{root}/tasks/{task_id}/events/stream",
        params={"event_cursor": "0-0"},
        timeout=httpx.Timeout(30, read=60),
    ) as response:
        response.raise_for_status()
        event_name = ""
        data: list[str] = []
        preview = ""
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif not line and data:
                payload = json.loads("\n".join(data))
                data.clear()
                if event_name == "stream.revoked":
                    print(f"task={task_id} access revoked", flush=True)
                    return
                if event_name != "task.event" or payload.get("event_type") != "run.live":
                    continue
                live = payload["payload"]
                kind = live["kind"]
                detail = live["data"]
                if kind.startswith("tool."):
                    print(f"[{kind}] {detail.get('tool_name', '')}", flush=True)
                elif kind == "output.reset":
                    preview = ""
                elif kind == "output.text":
                    text = detail.get("text", "")
                    if text != preview:
                        print(f"[output.preview] {text}", flush=True)
                        preview = text


async def run(args: argparse.Namespace) -> int:
    wid = os.environ["OPSMESH_WORKSPACE_ID"]
    prefix = os.getenv("OPSMESH_API_PREFIX", "/api/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['OPSMESH_TOKEN']}"}
    if os.getenv("OPSMESH_USER_ID"):
        headers["X-User-ID"] = os.environ["OPSMESH_USER_ID"]
    async with httpx.AsyncClient(
        base_url=os.getenv("OPSMESH_URL", "http://127.0.0.1:8000"), headers=headers, timeout=30
    ) as client:
        workspace_root = f"{prefix}/workspaces/{wid}"
        root = f"{workspace_root}/conversations"
        cid = args.conversation
        if cid is None:
            response = await client.post(
                root, json={"mode": "auto", "agent_profile_id": args.agent}
            )
            response.raise_for_status()
            cid = response.json()["id"]
        request_key = args.request_key or str(uuid4())
        print(f"conversation_id={cid}\nrequest_key={request_key}", flush=True)
        response = await client.post(
            f"{root}/{cid}/messages",
            json={"body": args.message},
            headers={"Idempotency-Key": request_key},
        )
        response.raise_for_status()
        turn = response.json()
        print(f"turn_id={turn['id']}", flush=True)
        deadline = asyncio.get_running_loop().time() + args.timeout
        streams: dict[str, asyncio.Task[None]] = {}
        previous_status = ""
        reported_streams: set[str] = set()
        try:
            while True:
                response = await client.get(
                    f"{root}/{cid}/executions", params={"limit": 100, "turn_id": turn["id"]}
                )
                response.raise_for_status()
                executions = [e for e in response.json()["items"] if e["turn_id"] == turn["id"]]
                for execution in executions:
                    task_id = execution["task_id"]
                    if task_id not in streams:
                        print(f"[{execution['purpose']}] task_id={task_id}", flush=True)
                        streams[task_id] = asyncio.create_task(
                            follow_task(client, workspace_root, task_id)
                        )
                    stream = streams[task_id]
                    if stream.done() and task_id not in reported_streams:
                        reported_streams.add(task_id)
                        if (error := stream.exception()) is not None:
                            print(
                                f"[event stream failed] task={task_id}: {type(error).__name__}",
                                flush=True,
                            )
                response = await client.get(
                    f"{root}/{cid}/messages", params={"offset": turn["sequence"] - 1, "limit": 1}
                )
                response.raise_for_status()
                items = response.json()["items"]
                if not items or items[0]["id"] != turn["id"]:
                    raise RuntimeError("Conversation turn is unavailable")
                turn = items[0]
                if turn["status"] != previous_status:
                    print(f"status={turn['status']}", flush=True)
                    previous_status = turn["status"]
                if turn["status"] == "completed":
                    print(turn["reply"] or "", flush=True)
                    return 0
                if turn["status"] in {"failed", "cancelled"}:
                    print(f"error_code={turn['error_code']}", flush=True)
                    if turn["error"]:
                        print(json.dumps(turn["error"], ensure_ascii=False), flush=True)
                    return 1
                if (
                    turn["status"] == "waiting_approval"
                    or asyncio.get_running_loop().time() >= deadline
                ):
                    print(f"inspect {root}/{cid}/executions", flush=True)
                    return 2
                await asyncio.sleep(1)
        finally:
            for stream in streams.values():
                stream.cancel()
            await asyncio.gather(*streams.values(), return_exceptions=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--conversation")
    target.add_argument("--agent", help="Manager Agent ID for a new auto conversation")
    parser.add_argument(
        "--request-key", default=None, help="Reuse this key to retry message delivery"
    )
    parser.add_argument("--timeout", type=float, default=300)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
