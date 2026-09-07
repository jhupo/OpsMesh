# Self-Hosted MCP Connector

`opsmesh-self-hosted-worker` runs approved stdio MCP servers on a workspace-owned machine. The
OpsMesh API remains the durable control plane; the connector owns only local process execution and
crash recovery.

## Install And Check

Install the independent runtime package on Python 3.11 or newer:

```bash
python -m pip install ./runtime
opsmesh-self-hosted-worker --check
```

The package pins MCP Python SDK `1.27.1`, calls its public `stdio_client`, `ClientSession`,
`initialize`, and `call_tool` interfaces, and does not implement MCP framing or JSON-RPC parsing.

## Register

Create a one-time enrollment token through the workspace API, then register the machine:

```bash
curl -X POST https://opsmesh.example.com/api/v1/self-hosted/register \
  -H 'Content-Type: application/json' \
  -d '{
    "enrollment_token": "ccrt_replace_with_enrollment_token",
    "name": "build-node-01",
    "machine_id": "stable-machine-id",
    "version": "0.1.0",
    "capabilities": {
      "allowed_tools": ["workspace.search"],
      "max_concurrent_mcp_jobs": 1
    }
  }'
```

Store the returned `credential_token` in the host secret manager. Do not put it in a command-line
argument, repository file, connector capability file, or log.

## Run

```bash
export OPSMESH_API_URL=https://opsmesh.example.com/api/v1
export OPSMESH_RUNTIME_CREDENTIAL=ccwc_replace_with_runtime_credential
opsmesh-self-hosted-worker --state-path /var/lib/opsmesh-connector/state.sqlite3
```

`OPSMESH_API_URL` includes the API prefix. HTTPS is mandatory except for loopback development. The
state directory must be private and persistent. The connector sets the SQLite file to owner-only
permissions where the operating system supports POSIX modes and uses a cross-platform file lock to
prevent two processes from sharing one recovery ledger.

The optional `--capabilities-file` must contain the complete capability object. Omitting it sends an
empty heartbeat object, which tells the control plane to preserve the capabilities established at
registration.

## Stdio MCP Credentials

Self-hosted stdio MCP servers resolve credentials from the Connector machine, not from the cloud.
Create an MCP credential reference with provider `self_hosted_env` and an external reference in the
form `env:VARIABLE_NAME`. The control plane queues and stores only that variable name. Immediately
before starting the MCP server, the Connector reads the value from its own process environment and
injects it into the child process.

Missing variables, malformed references, duplicate target names, hosted cloud credentials, and the
reserved `OPSMESH_RUNTIME_CREDENTIAL` variable fail closed. Raw values are never stored in the
control-plane MCP job, Connector recovery database, command arguments, completion payload, or error
message.

## Deployed Control-Plane Smoke

Run the packaged connector smoke from a release checkout on the self-hosted machine. It creates a
private temporary virtual environment, installs `runtime`, checks the pinned MCP SDK, authenticates
with the deployed API, sends a heartbeat, and performs one or more `--once` cycles:

```bash
export OPSMESH_API_URL=https://opsmesh.example.com/api/v1
export OPSMESH_RUNTIME_CREDENTIAL=ccwc_replace_with_runtime_credential
scripts/self-hosted-connector-smoke.sh
```

With a queued self-hosted MCP job, require an actual completion rather than an empty poll:

```bash
OPSMESH_CONNECTOR_EXPECTED_STATUS=completed \
    OPSMESH_CONNECTOR_SMOKE_TIMEOUT_SECONDS=120 \
    scripts/self-hosted-connector-smoke.sh
```

The script prints only the connector status JSON. It never prints the runtime credential or control
plane response bodies. Set `OPSMESH_CONNECTOR_SMOKE_DIR` to retain the private virtual environment
and SQLite recovery ledger for restart/replay verification.

## Execution And Recovery

The connector processes one MCP job at a time:

1. Heartbeat and poll for the next workspace-scoped job.
2. Claim the job through the idempotent claim endpoint.
3. Persist the request as `claimed`, then transition it to `executing`.
4. Validate the single supported v1 contract and invoke the official MCP SDK under a total timeout.
5. Persist the serialized SDK result as `result_ready` before posting completion.
6. Remove local state only after the completion endpoint confirms the same terminal status.

The completion contract is strict: `completed` requires a response payload and forbids an error;
`failed` requires an error payload and forbids a response. A repeated completion is accepted only
when its terminal status and payloads exactly match the stored result. A conflicting retry is
rejected, so a delayed or compromised worker cannot overwrite durable execution evidence.

After restart, a `result_ready` completion is posted again without re-executing the tool. A
`claimed` request that did not begin execution is resumed. An `executing` request has an uncertain
side-effect outcome, so it is completed with `self_hosted_mcp_execution_interrupted` instead of
being invoked twice. If the process stopped immediately after the API claim, the control plane
returns that worker's claimed job before new work so the connector can recreate its local record.

Transport failures, response bodies, SDK exception messages, tool arguments, and runtime
credentials are not copied into completion errors. Transient control-plane failures (`408`, `429`,
`5xx`, and transport failures) are retried; permanent HTTP failures and invalid connector
contracts stop the process while preserving the local recovery record for operator intervention.
Operators receive stable error codes while sensitive details remain local.
