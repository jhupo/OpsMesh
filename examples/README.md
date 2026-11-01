# Examples

The [custom plugin example](custom_plugin/README.md) runs locally without credentials,
paid model calls or Docker. It demonstrates event handlers, dependency scopes,
Agent factories and registered code capabilities.

Deployment settings use `OPSMESH_*` environment variables. See the working
[environment example](../.env.example) and [quick start](../README.md#快速开始).
Load an environment file explicitly with `uv run --env-file .env ...`.

Examples must state whether they call paid APIs or require Docker. Offline examples should remain executable without credentials.
