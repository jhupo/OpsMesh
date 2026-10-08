"""Submit and follow a durable OpsMesh conversation using a user API token."""

import argparse
import os
import time
from uuid import uuid4

import httpx


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
    args = parser.parse_args()
    token = os.environ["OPSMESH_TOKEN"]
    wid = os.environ["OPSMESH_WORKSPACE_ID"]
    prefix = os.getenv("OPSMESH_API_PREFIX", "/api/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    if os.getenv("OPSMESH_USER_ID"):
        headers["X-User-ID"] = os.environ["OPSMESH_USER_ID"]
    with httpx.Client(
        base_url=os.getenv("OPSMESH_URL", "http://127.0.0.1:8000"), headers=headers, timeout=30
    ) as client:
        root = f"{prefix}/workspaces/{wid}/conversations"
        cid = args.conversation
        if cid is None:
            response = client.post(root, json={"mode": "auto", "agent_profile_id": args.agent})
            response.raise_for_status()
            cid = response.json()["id"]
        request_key = args.request_key or str(uuid4())
        print(f"conversation_id={cid}\nrequest_key={request_key}", flush=True)
        response = client.post(
            f"{root}/{cid}/messages",
            json={"body": args.message},
            headers={"Idempotency-Key": request_key},
        )
        response.raise_for_status()
        turn = response.json()
        print(f"turn_id={turn['id']}", flush=True)
        deadline = time.monotonic() + args.timeout
        while True:
            response = client.get(
                f"{root}/{cid}/messages", params={"offset": turn["sequence"] - 1, "limit": 1}
            )
            response.raise_for_status()
            items = response.json()["items"]
            if not items or items[0]["id"] != turn["id"]:
                raise RuntimeError("Conversation turn is unavailable")
            turn = items[0]
            if turn["status"] == "completed":
                print(turn["reply"] or "")
                return 0
            if turn["status"] in {"failed", "cancelled"}:
                print(f"status={turn['status']} error_code={turn['error_code']}")
                return 1
            if turn["status"] == "waiting_approval" or time.monotonic() >= deadline:
                print(f"status={turn['status']}; inspect {root}/{cid}/executions")
                return 2
            time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
