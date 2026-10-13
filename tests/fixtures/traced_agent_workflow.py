"""Replayable SDK call graph, separate from the test receiver's external I/O."""

from datetime import timedelta

from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    from agents import Agent, RunConfig, Runner
    from temporalio.openai_agents.workflow import activity_as_tool

    from opsmesh.runtime.temporal.contracts import ExecutionRequest


@activity.defn
async def observe_count() -> str:
    return "private-tool-result-canary"


@workflow.defn(name="acceptance.traced_agent")
class TracedAgentWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> str:
        result = await Runner.run(
            Agent(
                name="Trace probe",
                model="offline",
                instructions="private-instructions-canary",
                tools=[
                    activity_as_tool(observe_count, start_to_close_timeout=timedelta(seconds=5))
                ],
            ),
            "private-prompt-canary",
            run_config=RunConfig(trace_include_sensitive_data=False),
            max_turns=3,
        )
        return str(result.final_output)
