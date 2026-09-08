# Adapter Boundaries

Consumers use product-owned Protocol contracts. Inheritance is reserved for a shared execution
template; independent infrastructure strategies use composition and registries.

## MCP

`McpToolAdapter` is the execution contract. `BaseRemoteMcpToolAdapter` owns remote URL validation,
credential resolution, and the retry/circuit boundary. HTTP and SSE select their official MCP SDK
transport. Docker and self-hosted stdio implementations retain their isolated execution paths;
they do not spawn processes on the control-plane host.

## Provider Health

`ProviderHealthProbe` defines asynchronous checks. `ProviderHealthRegistry` selects OpenAI-compatible
or Anthropic implementations by canonical provider key. Probe aggregation and existing HTTP health
request semantics remain unchanged. This refactor does not migrate health requests to vendor SDKs.

## Runtime Backend Selection

`RuntimeBackendRegistry` is used by the contextual MCP resolver after frozen runtime authorization.
The registered Docker and self-hosted implementations advertise MCP stdio, asynchronous job, and
managed container lifecycle capabilities. Selection uses the explicit runtime provider; a container
ID or runtime type cannot turn an unknown provider into Docker.

Hosted sandboxes are not implemented or registered. Unknown providers fail closed. This registry
currently unifies MCP runtime execution selection, not every provisioning or job lifecycle API.

## Validation

Focused adapter, health service, registry, gateway, and runtime authorization tests cover the change.
Four existing ordinary MCP execution tests in `test_agent_runtime_tools.py` also fail with the HEAD
resolver restored in memory; they are not counted as passing validation for this refactor.
