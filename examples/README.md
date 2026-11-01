# Examples

The [custom plugin example](custom_plugin/README.md) runs locally without credentials,
paid model calls or Docker. It demonstrates event handlers, dependency scopes,
Agent factories and registered code capabilities.

The [QQ community example](qq_community/README.md) provisions an Agent through
the platform, with a Docker sandbox, one upstream persona skill, a Jev tool and
native knowledge bindings. The platform supplies reviewed, batched conversation
and delivered Q&A capture. It requires Docker, QQ credentials and
paid Jev/Agent model API access. Its automated tests use local service doubles.

Deployment settings use `OPSMESH_*` environment variables. See the working
[environment example](../.env.example) and [quick start](../README.md#快速开始).
Load an environment file explicitly with `uv run --env-file .env ...`.

Examples must state whether they call paid APIs or require Docker. Offline examples should remain executable without credentials.
