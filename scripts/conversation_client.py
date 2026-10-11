"""Submit once and follow the conversation turn's native SSE contract."""

import argparse
import asyncio
import json
import os
from uuid import uuid4

import httpx


async def follow_turn(client: httpx.AsyncClient, endpoint: str) -> int:
    cursor = "0-0"
    previous_status = ""
    while True:
        try:
            async with client.stream(
                "GET",
                endpoint,
                headers={"Last-Event-ID": cursor},
                timeout=httpx.Timeout(30, read=60),
            ) as response:
                response.raise_for_status()
                event_name = ""
                pending_cursor = cursor
                data: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith("id:"):
                        pending_cursor = line[3:].strip()
                    elif line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].strip())
                    elif not line and data:
                        payload = json.loads("\n".join(data))
                        data.clear()
                        cursor = pending_cursor
                        if event_name in {"stream.revoked", "stream.unavailable"}:
                            print(f"[{event_name}]", flush=True)
                            return 1
                        if event_name == "stream.reset":
                            cursor = "0-0"
                            print("[output.reset]", flush=True)
                        elif event_name == "turn.snapshot":
                            turn = payload["turn"]
                            if turn["status"] != previous_status:
                                print(f"status={turn['status']}", flush=True)
                                previous_status = turn["status"]
                            if turn["status"] == "waiting_approval":
                                print(f"task_id={payload['task_id']} approval required", flush=True)
                                return 2
                        elif event_name == "task.event" and payload.get("event_type") == "run.live":
                            live = payload["payload"]
                            kind, detail = live["kind"], live["data"]
                            if kind == "output.text":
                                print(f"[output.preview] {detail.get('text', '')}", flush=True)
                            elif kind.startswith("tool."):
                                print(f"[{kind}] {detail.get('tool_name', '')}", flush=True)
                            else:
                                print(f"[{kind}]", flush=True)
                        elif event_name == "stream.completed":
                            turn = payload["turn"]
                            if turn["status"] == "completed":
                                print(turn["reply"] or "", flush=True)
                                return 0
                            print(f"error_code={turn['error_code']}", flush=True)
                            if turn["error"]:
                                print(json.dumps(turn["error"], ensure_ascii=False), flush=True)
                            return 1
        except httpx.TransportError as exc:
            print(f"[stream reconnect] {type(exc).__name__}", flush=True)
        # Reconnect only the read stream with its cursor. Never replay the POST.
        await asyncio.sleep(1)


async def run(args: argparse.Namespace) -> int:
    wid = os.environ["OPSMESH_WORKSPACE_ID"]
    prefix = os.getenv("OPSMESH_API_PREFIX", "/api/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['OPSMESH_TOKEN']}"}
    if os.getenv("OPSMESH_USER_ID"):
        headers["X-User-ID"] = os.environ["OPSMESH_USER_ID"]
    async with httpx.AsyncClient(
        base_url=os.getenv("OPSMESH_URL", "http://127.0.0.1:8000"),
        headers=headers,
        timeout=30,
    ) as client:
        root = f"{prefix}/workspaces/{wid}/conversations"
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
        print(f"turn_id={turn['id']}\nstatus={turn['status']}", flush=True)
        endpoint = f"{root}/{cid}/turns/{turn['id']}/events/stream"
        try:
            async with asyncio.timeout(args.timeout):
                return await follow_turn(client, endpoint)
        except TimeoutError:
            print(f"timeout; inspect {root}/{cid}/executions", flush=True)
            return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--conversation")
    target.add_argument("--agent", help="Entry Agent ID for a new auto conversation")
    parser.add_argument(
        "--request-key", default=None, help="Reuse this key for delivery idempotency"
    )
    parser.add_argument("--timeout", type=float, default=300)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
