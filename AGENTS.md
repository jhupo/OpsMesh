# OpsMesh development guide

OpsMesh is a plugin application host built on the OpenAI Agents Python SDK. Plugins own Agent construction and orchestration; the host manages identity, immutable publications, private resources, tasks, sessions and gateways. Start with [README.md](README.md) and the contracts in [docs/](docs/).

## Execution rules

- Run tests, test collection, acceptance scripts, real services and model calls only when explicitly requested by the user. Static inspection/checks and distribution builds are allowed. Report skipped and unverified work honestly.
- Preserve unrelated working changes and user data. Do not commit, push, tag, publish or delete user data without authorization. Do not introduce compatibility shims or silent fallbacks.
- SDK contracts are for plugin development/registration/packaging, not a business API client. Prefer the locked native SDK's Agent, Runner, RunState, Session, MCP, model and sandbox interfaces.
- Use `routes.py` for HTTP, `schemas.py` for request/response values, `models.py` for ORM, `service.py` for domain rules. Keep pure value/configuration models free of I/O.
- `setup(ctx)` only declares contributions. Network connections, subprocesses and Agent execution belong to invocation lifetimes. Installed Python plugins are trusted code.
- Pin installed plugin/configuration/dependency versions per task. Do not silently replace versions or replay failed work from the beginning. Resume only explicit checkpoints.
- Group membership permits invoking an Agent, never browsing/editing another user's workspace. Stored identity grants platform permissions; request metadata and gateway group roles cannot.
- Preserve authorization, encryption, idempotency, cancellation, human-input checks, audit and caller token/storage/concurrency accounting. Run completion does not delete persistent instances, sessions, sandboxes or directories.
- APIs do not hold gateway connections; workers do not import HTTP routes. Infrastructure does not assemble business models. Add database migrations without rewriting released history.

## Repository map

| Path | Responsibility |
| --- | --- |
| `src/opsmesh/agents`, `catalog`, `plugins` | Instances, immutable publications and plugin registration/loading |
| `src/opsmesh/tasks` | Task management, schedules, human input and persistent events |
| `src/opsmesh/tasks/runtime` | Leases, invocation contexts, execution and child calls |
| `src/opsmesh/sessions`, `resources` | Native history, sandboxes, private namespaces and files |
| `src/opsmesh/gateway` | QQ/DingTalk and custom channel delivery |
| `src/opsmesh/users`, `quotas` | Identity, connections and accounting |
| `src/opsmesh/shared`, `infra` | Cross-cutting errors/logging/auth and database/encryption/migrations |
| `src/opsmesh/bootstrap` | API/CLI/workers, initialization and deployment assembly |
| `sdk/python` | Independent typed plugin SDK (published separately on `sdk`) |
| `deploy`, `scripts`, `.github` | Linux distribution, installation and release/security automation |
| `examples`, `tests`, `docs` | Developer examples, regression code and contracts |

## Repository skills

Read the relevant skill before changing its integration:

| Skill | When to use |
| --- | --- |
| [.agents/skills/openai-knowledge](.agents/skills/openai-knowledge/SKILL.md) | Agents SDK interfaces, context, tools/MCP, sessions, RunState, sandbox or model integration |
| [.agents/skills/adapter-qq](.agents/skills/adapter-qq/SKILL.md) | QQ configuration, NoneBot drivers, events, messages, native APIs or connection lifecycle |
| [.agents/skills/nonebot](.agents/skills/nonebot/SKILL.md) | Driver composition, adapter reuse, event bridges and per-channel runtime isolation |
| [.agents/skills/dingtalk-sdk](.agents/skills/dingtalk-sdk/SKILL.md) | DingTalk Stream, robot/media/card/OpenAPI operations and callbacks |

Locked versions and installed source determine supported calls. Compare official documentation when needed; explain version differences. Skills do not authorize tests, publishing or external side effects.

## Static checks

Server Python >=3.12; SDK >=3.11.8. Use `uv sync --frozen --extra dev --extra sandbox --extra build`. Do not refresh dependencies for documentation-only changes.

```sh
uv run --no-sync ruff format --check src sdk/python/src sdk/python/examples examples tests scripts
uv run --no-sync ruff check src sdk/python/src sdk/python/examples examples tests scripts
uv run --no-sync mypy
uv run --no-sync lint-imports --no-cache
uv run --no-sync mypy --python-version 3.11 sdk/python/src
```

Use the SDK's own configuration when changing its independent project. HTTP documentation is generated from actual routes at `/openapi.json`. Keep README, deployment guidance and [verification boundaries](docs/verification.md) aligned. Delivery notes state actual static results, builds, tests executed and integration gaps separately. Releases are Linux-only; do not add Windows service installers. License is MIT; retain bundled third-party license notices.
