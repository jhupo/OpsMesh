"""Deterministic native SDK workflow for persistent-server acceptance."""

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from agents import Agent, RunConfig, Runner


@workflow.defn(name="opsmesh.acceptance.native_agent_persistence")
class PersistentAgentWorkflow:
    def __init__(self) -> None:
        self._answer: str | None = None
        self._released = False

    @workflow.run
    async def run(self) -> str:
        result = await Runner.run(
            Agent(name="Offline persistence probe", model="offline"),
            "Record the native model result once.",
            run_config=RunConfig(tracing_disabled=True),
            max_turns=2,
        )
        self._answer = str(result.final_output)
        await workflow.wait_condition(lambda: self._released)
        return self._answer

    @workflow.query
    def answer(self) -> str | None:
        return self._answer

    @workflow.signal
    def release(self) -> None:
        self._released = True
