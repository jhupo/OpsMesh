# Plugin SDK development

This project contains independent plugin contracts and a local packaging CLI. Do not import `opsmesh` host implementation modules or add business API clients. Native Agent, Session, RunState, tool, MCP and sandbox behavior belongs to the locked upstream Agents SDK, not replacement implementations here.

- Preserve unrelated changes and user data. Commit/push/publish only when requested.
- Tests, test collection, model calls, service starts and acceptance scripts require explicit user authorization. Static checks and packaging are allowed; skipped checks are not passes.
- Python >=3.11.8; public APIs must be typed. Keep `py.typed` in wheels. Optional native Agent support lives in the `agents` extra; packaging-only users should not require it.
- Registration is declarative. `setup(ctx)` does not perform I/O or execute agent logic. Invocation context identity comes from the host; metadata is untrusted application input. Groups never imply workspace sharing.
- Use `uv sync --extra dev --extra agents`, then `uv run ruff format --check src examples`, `uv run ruff check src examples`, `uv run mypy`. `uv build` produces wheel/sdist. Do not claim packaging proves runtime behavior.
- Keep README/examples/configuration and version aligned. Main development happens in `sdk/python` on the host branch; the independent `sdk` branch is an explicitly published snapshot.
- License is LGPL-3.0-only. Do not add compatibility aliases or host shims to bypass boundary checks.
