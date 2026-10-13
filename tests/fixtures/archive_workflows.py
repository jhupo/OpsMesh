"""Minimal deterministic Workflows used for archive queue capacity verification."""

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from opsmesh.runtime.temporal.contracts import ExecutionRequest


@workflow.defn(name="archive-control-probe")
class ArchiveControlProbe:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> str:
        return await workflow.execute_activity(
            "opsmesh.archive.prepare",
            request,
            result_type=str,
            start_to_close_timeout=timedelta(seconds=15),
        )
