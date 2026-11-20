# Focused Verification

The current consumer-plugin example is covered by
`tests/unit/test_example_plugin.py`. It verifies that a wheel-installed plugin
can load file-backed configuration without importing the removed generic
`channels` package. The standalone adapter contracts are covered by
`tests/unit/test_qq_adapter.py` and `tests/unit/test_dingtalk_adapter.py`.

Run the current example and adapter checks together with:

```bash
python -m pytest tests/unit/test_example_plugin.py tests/unit/test_qq_adapter.py tests/unit/test_dingtalk_adapter.py -q
```

Install the current framework, service and plugins into a test virtual
environment before running `python -m pytest tests/unit -q`. Use
`requirements.lock` and `requirements-dev.lock` for test dependencies.
The old framework, Channels, Cron, Execution and consumer tests in
[`legacy/`](legacy/) remain as migration reference and are not collected.
See the [framework Gateway interfaces](../docs/gateway.md) for current behavior.

Run the unit modules affected by a change. Deterministic knowledge tests use a
fake encoder and never download model weights or call a model service.

## Isolated PostgreSQL

The optional PostgreSQL tests require PostgreSQL 17 and an already installed
`vector` extension for knowledge. A disposable configuration is provided:

```bash
docker compose -f tests/compose.yaml up -d --wait
export OPSMESH_TEST_DSN=postgresql+asyncpg://opsmesh_test:isolated-test-password@127.0.0.1:55432/opsmesh_test
python -m pytest tests/integration/test_knowledge_postgresql.py -q
docker compose -f tests/compose.yaml down
```

The service listens on loopback and keeps its database in temporary memory.
Every database test creates its own random schema and removes only that schema.
The configured account must be permitted to create schemas; use a dedicated test
database. Without `OPSMESH_TEST_DSN`, these tests are skipped.

Install the plugin packages under test first. Knowledge's local inference
dependencies are optional for deterministic tests; real embedding verification
requires `packaging/knowledge.lock` and the separately provisioned, verified
model directory. See the knowledge README.

Real robot accounts, model endpoints
and Langfuse require separate integration verification. A passing unit suite
does not establish those external services are working.
