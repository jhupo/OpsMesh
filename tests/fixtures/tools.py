"""Exercise transaction preparation, detached Runtime wait and durable completion."""

import asyncio

from opsmesh.agents.execution.tools.executor import BackendToolExecutor
from opsmesh.capabilities.mcp.execution.prepared import PreparedMcpTool
from opsmesh.shared.concurrency import BlockingIO


def execute_tool(executor: BackendToolExecutor, **arguments):
    prepared = executor.prepare_tool(**arguments)
    executor._session.commit()
    if not isinstance(prepared, PreparedMcpTool):
        return prepared

    async def remote():
        with BlockingIO(1, name="test-tool-control") as io:
            return await prepared.execution.operation.execute(io)

    response, error = None, None
    try:
        response = asyncio.run(remote())
    except BaseException as failure:
        error = failure
    result = executor.complete_tool(prepared, response, error)
    executor._session.commit()
    return result
