# opsmesh-execution

> **Migration note:** This README describes the pre-restoration implementation. Its commands and examples do not target the restored NoneBot2 core. See the [repository overview](../../README.md) for current interfaces and migration status.

Independent OpsMesh plugin that runs native OpenAI Agents SDK `Agent` and `SandboxAgent` objects. The import module is `opsmesh.plugins.execution`.

Execution provides run/stream lifetime management, framework dependency-aware callbacks and optional tracing. It does not require the database, filesystem or channels plugins. It does not construct agents, store conversation indexes or persist usage ledgers.

## Installation

Install matching framework and plugin wheels in a regular Python environment. For local development from the repository root:

```bash
python -m pip install -e . -e packages/execution
# Optional tracing integrations:
python -m pip install -e "packages/execution[langfuse]"
```

The framework wheel does not include this plugin or the Agents SDK.

## Use in a plugin

```python
from agents import Agent
from opsmesh.plugins import require

execution = require("execution")
agent = Agent(name="Assistant", instructions="Answer clearly.")


async def answer(message: str) -> str:
    result = await execution.run(agent, message)
    return result.final_output
```

Framework Matchers supply invocation ownership; background calls pass the consumer's `plugin_id` explicitly. User context, native Session, RunConfig, hooks and RunState are supplied by the consumer. `stream()` is an async context manager: consume the native result inside its scope. Framework invocation scopes keep the consumer and its dependencies alive through callback and stream cleanup.

`before_run` can reject a call before Runner starts. SDK hook decorators preserve native failures. `on_run_end` receives native results/errors for observation; observer failures do not replace a completed result, while cancellation from the caller is still propagated. Read Usage from native SDK results or model-response callbacks.

Native composition such as `Agent.as_tool()` and handoffs remains within the outer SDK run. A separate task calling `execution.run()` or `stream()` must pass admission again; inherited context does not authorize new work during draining. Stream cleanup has a configurable budget. SDK work that survives cancellation retains its plugin resources until it actually ends.

Tracing can be disabled, local or Langfuse. The execution plugin's sensitive-data setting is an upper bound. Supply explicit `TraceContext` attributes for Langfuse users and sessions. `diagnostics()` returns the selected destination and a detached snapshot of bounded local counters and span summaries.

## License

MIT; see the package's included license files and third-party notices.
