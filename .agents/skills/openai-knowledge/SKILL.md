---
name: openai-knowledge
description: Verify OpenAI Agents Python SDK interfaces, context, tools, MCP, sessions, RunState, sandbox and model integration when changing OpsMesh execution or plugin contracts.
---

# OpenAI Agents integration

1. Resolve the exact `openai-agents` and `openai` versions from `uv.lock` and inspect the installed implementation before changing a call. Current upstream documentation can describe a newer release.
2. Use official sources: https://openai.github.io/openai-agents-python/ and https://github.com/openai/openai-agents-python. Check the relevant reference and source, not examples alone.
3. Prefer native `Agent`, `Runner`, `RunContextWrapper`, `Session`, `RunState`, MCP and sandbox protocols. Do not implement parallel model dispatch, history or checkpoint engines.
4. Invocation context contains local dependencies and trusted identity. It is not automatically visible to the model. Enforce permissions in host/tool implementations; user metadata and channel roles never grant platform authority.
5. Keep Agent configuration, persistent sessions/sandboxes, and individual task attempts separate. Completing or cancelling an attempt does not delete persistent resources.
6. Preserve pinned publications, caller accounting, cancellation and human-input/checkpoint contracts. Never silently restart a failed run as recovery.

Follow the repository's explicit authorization rule for tests and real model/service calls. Report version differences and unverified integration paths.
