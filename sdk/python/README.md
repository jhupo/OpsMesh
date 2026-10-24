# OpsMesh Plugin SDK

[![SDK checks](https://github.com/jhupo/OpsMesh/actions/workflows/ci.yml/badge.svg?branch=sdk)](https://github.com/jhupo/OpsMesh/actions?query=branch%3Asdk)

Typed, independent contracts for OpsMesh plugin development, registration and ZIP packaging. The SDK does not depend on the host implementation and does not provide a business API client. Python **3.11.8+** is supported; native Agent examples target `openai-agents==0.22.3`.

This directory is published at the root of the repository's [`sdk` branch](https://github.com/jhupo/OpsMesh/tree/sdk). Matching SDK wheel and sdist files are attached to server Releases. There is no configured PyPI publication; do not assume the package is already available there.

## Installation

Download a matching wheel from [Releases](https://github.com/jhupo/OpsMesh/releases), verify its entry in `SHA256SUMS`, then install:

```sh
pip install ./opsmesh_sdk-0.2.0-py3-none-any.whl
# Native Agent support is optional for packaging-only development:
pip install 'openai-agents==0.22.3'
# Or install directly from the independent branch:
pip install 'git+https://github.com/jhupo/OpsMesh.git@sdk'
```

For reproducible deployments, pin a wheel hash or a Git commit rather than a moving branch. Installing the SDK does not start the host or call a model. Host dependencies for plugins must be provisioned by the operator; plugin ZIP import never invokes pip or install scripts.

## Minimal plugin

A plugin contains `plugin.yaml`, `main.py` and optional local modules or skill directories. The included `examples/echo` works with this SDK alone. An Agent plugin can import the native Agents SDK directly:

```python
from typing import Any

from agents import Agent, Runner
from opsmesh_sdk import InvocationContext, PluginContext


async def invoke(context: InvocationContext, value: Any) -> Any:
    model = await context.resources.model()
    agent = Agent[InvocationContext](name="Assistant", model=model)
    result = await Runner.run(
        agent, value, context=context, session=context.session.history
    )
    return result.final_output


def setup(context: PluginContext) -> None:
    context.entry("chat", invoke)
```

```yaml
id: my-assistant
version: 1.0.0
name: My assistant
```

Package from the independent SDK checkout:

```sh
opsmesh-plugin package ./examples/echo ./echo.zip
# Or package your own directory:
opsmesh-plugin package ./my-plugin ./my-plugin.zip
```

An administrator imports the ZIP into the host and creates an instance referencing the registered entry. The host's main-tree `examples/native_agent` demonstrates streaming, tools, MCP dependencies and native approval/RunState recovery; `examples/sandbox_agent` demonstrates the native sandbox interface.

## Registration and invocation contracts

`setup(context)` synchronously declares `entry`, `tool`, `mcp`, `skill`, `channel` and `sandbox` contributions. It must not connect MCP, start processes or execute Agent logic. Handlers are asynchronous and return JSON-compatible output or `Suspended`. Resume handlers must be explicitly registered when suspension/checkpoints are supported. Modules use relative imports within their isolated installed package.

`InvocationContext` provides trusted `user` (`user_id`, platform roles, group IDs), task/attempt IDs, configuration, metadata, routing source, `resources`, `dependencies`, `interactions`, `events`, `session` and optional checkpoint/decision. Caller metadata is not a platform authority source. External QQ/DingTalk group roles do not become platform roles.

- `resources.model()` selects a metered native model from an explicit connection, the agent owner's default or the host environment. `model_provider(...)` exposes native MultiProvider with registered prefixes. Token accounting remains with the original caller.
- `resources.data_dir` is persistent agent storage; `workspace` is optional and private. Use `artifact(...)` to copy selected files into caller-visible results. Using an agent never grants access to the owner's private workspace.
- `dependencies.tool(alias)`, `async with dependencies.mcp(alias)`, `dependencies.skill(alias)` and `dependencies.call(alias, value, key=...)` resolve declared immutable publications. Child call keys identify a stable input/operation, not a replay policy.
- `session.history` is a native encrypted Session exposing `get_items`, `add_items`, `pop_item` and `clear_session`. Clearing history does not delete files or checkpoints. Pass it to native Runner methods.
- `async with session.sandbox(client, options, manifest=...)` provides native `SandboxRunConfig`, persistent backend state and snapshots. Different private/group conversations have separate sessions; one session is serialized.
- `await events.emit("progress", {"step": 1})` writes a bounded JSON progress event, delivered as `plugin.progress` by the host SSE route. Native streaming is consumed by plugin code using `Runner.run_streamed`; the SDK does not wrap or replace Runner.
- `await interactions.checkpoint({...})` persists an explicit recovery point. Return `Suspended(checkpoint=..., prompt=...)` to request human input. Store native RunState JSON with an explicit context serializer, and restore with a newly constructed context override. Arbitrary Python stacks cannot be resumed.

Tools register native `FunctionTool` objects, retaining native approvals, guardrails and timeouts. MCP factories create a fresh native MCP server per invocation. Sandbox factories accept `SandboxContext` and yield a native client through an async context manager; task execution and file management share that backend.

Custom channels implement `ChannelAdapter.connect`, `send`, `execute`, `close` and the JSON `capabilities` property. Call `super().__init__()` and set `ready` only once sending is possible. `connect` lasts for the connection lifetime and publishes `ChannelEvent` values to its sink. Set `is_message=True` only for user messages eligible to invoke an agent; other events are recorded without execution. `OutgoingMessage.content` carries structured segments. `send` and `execute` return JSON receipts, raise `OperationRejected(code)` for definite rejection, and propagate transport failures so the host can mark uncertain effects. IDs must be stable across retries. Credentials stay in encrypted `reply_target`; invocations receive public event metadata.

Gateway invocations also expose optional `context.channel`. `await context.channel.reply("hello", key="reply-1")` queues a reply to the current conversation and returns a delivery ID; `content=[...]` supports native rich segments. `await context.channel.request(operation, parameters, key="action-1")` queues an operation and returns its ID, requiring the caller's current platform administrator role. The worker revalidates that role before execution. HTTP invocations have no channel. Both methods enqueue durable work; their return value does not claim platform delivery.

Installed Python plugins are trusted executable code. These protocols are not an operating system sandbox or a guarantee of idempotent external effects.

## Development

```sh
uv sync --extra dev --extra agents
uv run ruff format --check src examples
uv run ruff check src examples
uv run mypy
uv build
```

This is a library project; its standalone CI resolves declared dependency bounds. The host release uses the main tree's `uv.lock` for runtime dependencies. Tests/model calls/service acceptance require explicit user authorization for automated contributors. See [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md) and [SECURITY.md](SECURITY.md).

## License

MIT; see [LICENSE](LICENSE). Third-party dependencies retain their own copyright and license notices.
