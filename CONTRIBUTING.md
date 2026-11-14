# Contributing to OpsMesh

OpsMesh is an early-stage project. Public APIs can change before a stable release; describe breaking changes and update every affected caller in the same change.

## Development setup

Use Python 3.11 or newer in a virtual environment. Install the repository's development lock, then the packages you are modifying:

```bash
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e . -e packages/channels \
  -e packages/filesystem -e packages/database -e packages/execution -e packages/cron
```

To install only the framework package, use `python -m pip install -e .`. The repository development lock intentionally includes plugin test dependencies; it is not the core runtime dependency list. Linux filesystem tests require the supported Linux system calls.

Knowledge has a separate local embedding dependency lock. For knowledge work,
install `packaging/knowledge.lock` and the `packages/knowledge` project as well.
Its deterministic tests use a fake encoder; downloading model weights is a
separate, explicit provisioning step described in the plugin README.

## Package boundaries

- `src/opsmesh` owns the framework, driver, host and CLI. It must start with an empty plugin manifest and without official plugin dependencies.
- `packages/*` are separately installable distributions with their own dependency declarations and READMEs. A plugin may use framework interfaces; the framework must not import a plugin.
- `require()` expresses plugin loading and lifecycle dependencies. `Depends()` resolves invocation parameters and scoped resources.
- Agent plugins define native SDK objects. Execution adapts native runs and observation; it does not own business sessions or usage ledgers.
- The installer manages only the framework service. Plugin installation and database migrations are explicit administrative actions.

See `docs/plugin-architecture.md` for the runtime contracts. Do not duplicate SDK models or introduce forwarding layers without a concrete requirement.

## Validation

Run tests for the affected behavior rather than repeatedly running the full suite. Include failure, cancellation and cleanup paths for lifecycle changes. Examples:

```bash
python -m pytest tests/unit/test_execution_runner.py -q
python -m ruff check packages/execution/src tests/unit/test_execution_runner.py
python -m ruff format --check packages/execution/src tests/unit/test_execution_runner.py
```

Packaging changes also need wheel content and clean-install checks. Release changes need the Linux executable smoke test; deployment changes need installation and rollback checks in a disposable Linux environment. Never use production data for these tests. State which checks ran and which external integrations were not exercised.

The repository-wide CI can run broader checks. Do not update unrelated code merely to silence a pre-existing failure; report it separately with a reproducible case.

## Pull requests

Describe the problem, final behavior, affected packages and validation. Update API examples, configuration, docs and release contracts when their interfaces change. Keep source comments, docstrings and error messages in English, with Google-style docstrings where explanation is useful.

Use native SDK or library types where possible. Put implementation details in responsibility-specific modules; a leading underscore marks internal interfaces, not a mandatory directory hierarchy. Do not add compatibility aliases for removed interfaces unless the change explicitly requires them.

Do not commit credentials, private endpoints, virtual environments, backups, build output or runtime data. Preserve third-party copyright and license notices. Follow `SECURITY.md` for security-sensitive reports.
