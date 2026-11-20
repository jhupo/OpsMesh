# Contributing to OpsMesh

OpsMesh is an early-stage project. Public APIs can change before a stable release; describe breaking changes and update every affected caller in the same change.

## Development setup

Use Python 3.11 or newer in a virtual environment. For core development, install the framework from the repository root:

```bash
python -m pip install -e .
```

Cron, Execution and legacy examples still target the pre-restoration interfaces and are not published with the restored core. Install and verify the current packages as described in [the test guide](tests/README.md). Linux filesystem tests require the supported Linux system calls.

Knowledge has a separate local embedding dependency lock. For knowledge work,
install `packaging/knowledge.lock` and the `packages/knowledge` project as well.
Its deterministic tests use a fake encoder; downloading model weights is a
separate, explicit provisioning step described in the plugin README.

## Package boundaries

- `src/opsmesh` owns the framework and Driver. It must be usable without any official plugins. `packages/*` contains plugins; the framework must not import a business plugin.
- `require()` loads a plugin dependency and returns its module. `Depends()` resolves invocation parameters and scoped resources; it is not a plugin loader.
- Agent plugins define native SDK objects. Execution adapts native runs and observation; it does not own business sessions or usage ledgers.

For current framework behavior, consult the [framework architecture](docs/framework.md), [gateway flow](docs/gateway.md), [version-matched NoneBot2 documentation index](.agents/skills/opsmesh-nonebot2/SKILL.md), and [local source](src/opsmesh/). The framework Gateway rename is complete; HTTP business adapters are separate pending work. When a refactor is completed, update the maintained architecture documentation, preserve still-needed pending work in its owning design, delete the superseded refactoring plan, and fix its references. Do not duplicate SDK models or introduce forwarding layers without a concrete requirement.

## Validation

Run tests for the affected behavior rather than repeatedly running the full suite. Include failure, cancellation and cleanup paths for lifecycle changes. Examples:

```bash
python -m pytest tests/unit/test_plugin_file_configuration.py -q
```

Packaging changes also need wheel content and clean-install checks. Never use production data for these tests. State which checks ran and which external integrations were not exercised.

The repository-wide CI can run broader checks. Do not update unrelated code merely to silence a pre-existing failure; report it separately with a reproducible case.

## Pull requests

Describe the problem, final behavior, affected packages and validation. Update API examples, configuration, docs and release contracts when their interfaces change. Keep source comments, docstrings and error messages in English, with Google-style docstrings where explanation is useful.

Use native SDK or library types where possible. Put implementation details in responsibility-specific modules; a leading underscore marks internal interfaces, not a mandatory directory hierarchy. Do not add compatibility aliases for removed interfaces unless the change explicitly requires them.

Do not commit credentials, private endpoints, virtual environments, backups, build output or runtime data. Preserve third-party copyright and license notices. Follow `SECURITY.md` for security-sensitive reports.
