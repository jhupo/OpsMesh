"""Submit a configured Agent and follow its private Session's Run events through the SDK.

Set OPSMESH_URL and OPSMESH_SESSION_ID; OPSMESH_API_PREFIX defaults to /api/v1.
Submit: python scripts/configuration_client.py --configuration UUID "hello"
Resume reading: python scripts/configuration_client.py --run UUID --after 12
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from uuid import UUID, uuid4

from opsmesh_sdk.api.orchestration import (
    ConfigurationRunCreate,
    OrchestrationClient,
    RunView,
    create_orchestration_client,
)
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.errors import OpsMeshError, TransportError

TERMINAL = frozenset({"succeeded", "failed", "cancelled", "timed_out", "needs_attention"})


def result(run: RunView) -> int:
    print(f"status={run.status}", flush=True)
    if run.status == "succeeded":
        print(
            json.dumps({"output": run.output, "artifacts": run.artifacts}, default=str), flush=True
        )
        return 0
    if run.status == "waiting_approval":
        return 2
    print(f"error_code={run.error_code}", flush=True)
    return 1


async def follow_run(api: OrchestrationClient, run_id: UUID, *, after: int = 0) -> int:
    cursor = after
    while True:
        try:
            async for event in api.runs.events.stream(run_id, after=cursor):
                if event.sequence <= cursor:
                    continue
                cursor = event.sequence
                print(event.model_dump_json(), flush=True)
                if event.kind == "waiting_approval":
                    return 2
            current = await api.runs.get(run_id)
            if current.status in TERMINAL or current.status == "waiting_approval":
                return result(current)
        except TransportError as exc:
            status = exc.details.get("status")
            if isinstance(status, int) and status < 500:
                raise
            print(f"stream reconnect; run_id={run_id} after={cursor}", flush=True)
        # A reconnect only reads events; it never repeats the submission.
        await asyncio.sleep(1)


async def run(args: argparse.Namespace) -> int:
    session_token = os.environ.get("OPSMESH_SESSION_ID")
    if not session_token:
        raise ValueError("OPSMESH_SESSION_ID is required")
    async with PlatformClient(
        os.environ.get("OPSMESH_URL", "http://127.0.0.1:8000"),
        session_id=session_token,
        api_prefix=os.environ.get("OPSMESH_API_PREFIX", "/api/v1").rstrip("/"),
    ) as client:
        api = create_orchestration_client(client)
        if args.configuration is not None:
            revision = args.revision
            if revision is None:
                revision = (await api.configurations.get(args.configuration)).revision
            request_key = args.request_key or str(uuid4())
            print(f"request_key={request_key}", flush=True)
            current = await api.configurations.runs.create(
                args.configuration,
                ConfigurationRunCreate(
                    configuration_revision=revision,
                    session_id=args.session,
                    input=args.message,
                ),
                idempotency_key=request_key,
            )
            run_id = current.run_id
            print(f"session_id={current.session_id}\nrun_id={run_id}", flush=True)
            if current.status in TERMINAL or current.status == "waiting_approval":
                return result(current)
        else:
            run_id = args.run
            current = await api.runs.get(run_id)
            print(f"session_id={current.session_id}\nrun_id={run_id}", flush=True)
            if current.status in TERMINAL or current.status == "waiting_approval":
                return result(current)
        try:
            async with asyncio.timeout(args.timeout):
                return await follow_run(api, run_id, after=args.after)
        except TimeoutError:
            print(f"timeout; follow existing Run with --run {run_id}", flush=True)
            return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="?")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--configuration", type=UUID)
    target.add_argument("--run", type=UUID, help="Read an existing Run without submitting")
    parser.add_argument("--session", type=UUID, help="Reuse an owned private model Session")
    parser.add_argument(
        "--revision", type=int, help="Default: read the current configuration revision"
    )
    parser.add_argument("--request-key", help="Stable submission idempotency key")
    parser.add_argument("--after", type=int, default=0, help="Resume after this Run event sequence")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args(argv)
    if args.configuration is not None and args.message is None:
        parser.error("A message is required when submitting a configuration")
    if args.run is not None and any(
        value is not None for value in (args.message, args.session, args.revision, args.request_key)
    ):
        parser.error("--run only reads an existing Run; omit submission arguments")
    if args.after < 0 or args.timeout <= 0 or (args.revision is not None and args.revision < 1):
        parser.error("--after must be nonnegative; --timeout and --revision must be positive")
    try:
        return asyncio.run(run(args))
    except (OpsMeshError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
