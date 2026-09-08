# Shared Domain Services

## Approval Waiting

Product, MCP, and runtime tools use `ApprovalWaitingService`. It validates workspace ownership and
the run/task association before changing state. Only running tasks transition to waiting approval;
the run state machine validates run transitions. Callers retain transaction ownership.

## Run Events

Orchestration, self-hosted workers, product tools, and runtime tools use `RunEventWriter` for sequence
allocation, text/metadata redaction, and trace metadata. The writer locks the scoped parent run with
SQLAlchemy `SELECT FOR UPDATE`, flushes pending work, allocates the sequence, and flushes the event.
The caller owns commit/rollback and must keep the transaction short. Domain recorders retain event
content construction, not duplicate persistence algorithms.

## Upload Compensation

Workspace uploads reuse `CompensatingObjectStorageWrites`, as artifacts and archive import do.
New uploads use a unique file UUID in the workspace storage key. On failure the caller rolls back
metadata and compensates new objects, preserving earlier identical uploads. Existing rows continue
to resolve their stored keys; there is no legacy-key branch or compatibility adapter.

## Self-Hosted Policy Values

Trust summaries and artifact checks use `positive_policy_int` from the self-hosted policy module.
Only positive integers are accepted; booleans, strings, zero, negative and fractional values return
no positive value. This preserves optional-field handling while excluding Python bool-as-int.

## Shared Redaction

Security owns one set of sensitive field and text detectors, including GitHub/AWS credentials,
encrypted credential fields, and provider URL assignments. Teams, operations, memory capture,
audit and webhooks use it directly. Text rendering explicitly chooses whole-value masking or
fragment masking; both modes share detection and recursive traversal. Webhook payloads are
redacted before signing, and response snippets are redacted before truncation. Superseded
domain-specific redaction implementations and exports have been removed.

## Validation Limits

The PostgreSQL concurrency test uses `OPSMESH_TEST_POSTGRES_URL` and must run separately from tests
that patch model types for SQLite. The current local environment has no configured test URL or
Docker executable, so that test is skipped, not reported as concurrency verification.
The self-hosted capability job-claim test fails with both the original and consolidated event writer;
the pre-existing failure is not treated as passing validation.
