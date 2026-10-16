"""Sandbox-safe workflow and activity definitions for codec replay verification.

This module must stay import-clean (temporalio and the standard library only):
the Temporal sandbox re-imports it when validating and replaying the workflow.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import activity, workflow

TASK_QUEUE = "opsmesh-codec-verification"


@activity.defn
async def echo_activity(payload: dict[str, str]) -> dict[str, str]:
    return payload


@workflow.defn
class EchoWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, str]) -> dict[str, str]:
        return await workflow.execute_activity(
            echo_activity,
            payload,
            task_queue=TASK_QUEUE,
            start_to_close_timeout=timedelta(seconds=30),
        )
