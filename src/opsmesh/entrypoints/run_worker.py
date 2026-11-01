"""Execute one persisted run in a dedicated worker process without queue consumers."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.framework.errors import OpsMeshError


def _diagnostic(event: str, run_id: str, **facts: str) -> None:
    print(
        json.dumps(
            {
                "event": event,
                "run_id": run_id,
                "occurred_at": datetime.now(UTC).isoformat(),
                **facts,
            }
        ),
        flush=True,
    )


async def execute_run(
    run_id: str, settings: OpsMeshSettings, launch_token: str | None = None
) -> int:
    _diagnostic("worker.started", run_id)
    application = OpsMeshApplication(
        settings.model_copy(update={"worker_enabled": False, "run_worker_mode": "inline"})
    )
    try:
        # start verifies an already initialized schema and loads the same configured plugins.
        await application.start()
        outcome = await application.services.runtime.start(run_id, launch_token=launch_token)
        _diagnostic("worker.finished", run_id, status=outcome.status)
        return 0 if outcome.status in {"completed", "waiting_approval", "cancelled"} else 1
    finally:
        await application.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    arguments = parser.parse_args()
    payload = os.environ.get("OPSMESH_WORKER_SETTINGS")
    settings = (
        OpsMeshSettings.model_validate_json(payload)
        if payload
        else OpsMeshSettings.from_environment()
    )
    try:
        result = asyncio.run(
            execute_run(arguments.run_id, settings, os.environ.get("OPSMESH_RUN_LAUNCH_TOKEN"))
        )
    except OpsMeshError as error:
        _diagnostic("worker.error", arguments.run_id, code=error.code)
        result = 1
    raise SystemExit(result)


if __name__ == "__main__":
    main()
