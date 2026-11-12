# opsmesh-execution

Independent OpsMesh plugin that runs native OpenAI Agents SDK `Agent` and `SandboxAgent` objects. The import module and configuration namespace are `opsmesh.plugins.execution`.

Execution provides run/stream lifetime management, bounded cleanup, framework dependency-aware callbacks and optional tracing. It does not require the database, filesystem or channels plugins. It does not construct agents, store conversation indexes or persist usage ledgers.

## Installation

Install matching framework and plugin wheels in a regular Python environment. For local development from the repository root:

```bash
python -m pip install -e . -e packages/execution
# Optional tracing integrations:
python -m pip install -e "packages/execution[langfuse]"
```

The Linux executable host uses the separate official plugin runtime archive, which contains compatible dependencies. The framework archive itself contains neither this plugin nor the Agents SDK.

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

The host manifest must allow execution and the consumer plugin. Framework handlers supply invocation ownership; background calls pass the consumer's `plugin_id` explicitly. User context, native Session, RunConfig, hooks and RunState are supplied by the consumer. `stream()` is an async context manager: consume the native result inside its scope.

`before_run` can reject a call before Runner starts. SDK hook decorators preserve native failures. `on_run_end` receives native results/errors for observation; observer failures do not replace a completed result, while cancellation from the caller is still propagated. Read Usage from native SDK results or model-response callbacks.

Tracing can be disabled, local or Langfuse. The host sensitive-data setting is an upper bound. Supply explicit `TraceContext` attributes for Langfuse users and sessions. The pinned SDK/OpenInference approval-resume limitation and an explicit new-trace workaround are documented in `docs/langfuse-tracing.md`.

## License

MIT; see the package's included license files and third-party notices.
