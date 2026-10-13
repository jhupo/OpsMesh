"""Exercise authorized product tools at their Activity transaction boundary."""

from opsmesh.agents.execution.tools.executor import AgentToolExecutionService


def execute_tool(executor: AgentToolExecutionService, **arguments):
    result = executor.execute_tool(**arguments)
    executor._session.commit()
    return result
