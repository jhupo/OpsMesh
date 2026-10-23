"""Temporal adapter for explicit sandbox lifecycle commands."""

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from opsmesh.workflows.engines.temporal.agent_bindings import ACTIVITY_OPTIONS


@workflow.defn
class InstanceSandboxWorkflow:
    @workflow.run
    async def run(self, command: dict[str, str]) -> str:
        result: str = await workflow.execute_activity(
            "execution.instance_sandbox_operation",
            command,
            result_type=str,
            **ACTIVITY_OPTIONS,
        )
        return result
