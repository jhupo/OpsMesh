# Regression tests

Run tests only with explicit user authorization, as required by root `AGENTS.md`. CI's declared regression job runs against an isolated PostgreSQL service.

```sh
uv run --no-sync python -m unittest discover -s tests -v
```

`test_gateway.py` covers native QQ events/APIs, signed webhook parsing, rich messages, DingTalk callbacks/OpenAPI/media, and durable event/operation behavior. `test_regressions.py` covers SDK tool options, usage reservation, task cancellation, child capacity and checkpoint/session/quota regressions.

`test_initialization.py` checks owner-only initial credential files on POSIX, refusal to overwrite existing files and cleanup after incomplete writes. Credentials are not printed to process logs.

Database cases require `OPSMESH_TEST_DATABASE_URL`, using a PostgreSQL URL with the `postgresql+psycopg` scheme. They create and drop unique temporary schemas. Point this variable at an explicitly provisioned disposable database, never a production database. Without it, 11 database cases are skipped and must not be reported as passed.

HTTP transports and callback payloads are simulated in unit tests. They do not establish external platform permission, live QQ/DingTalk delivery, actual model usage, sandbox backend behavior or release infrastructure. Record live validation separately in [verification boundaries](../docs/verification.md).
