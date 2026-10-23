# TypeSafe Decision Plugin

This optional plugin evaluates relevance and whether a reply is requested, then selects
one configured handler. It uses the official `typesafe-sdk==0.7.5` and `jev-latest`.
It does not implement authorization, execute tools chosen by the model, or install QQ
credentials. The gateway has no TypeSafe dependency in its processing code.

## Install And Configure

Build this directory using `opsmesh-plugin build`, install the ZIP through the normal
plugin API or CLI, configure the plugin, then enable it. The host includes the locked
vendor runtime in its server distribution; the plugin SDK itself has no vendor dependency.
No plugin is installed or enabled automatically.

Installation configuration, using credential references rather than secret values:

```json
{
  "api_key_ref": "env:TYPESAFE_API_KEY",
  "model": "jev-latest",
  "network_mode": "proxy",
  "proxy_ref": "env:TYPESAFE_PROXY_URL",
  "max_retries": 2,
  "timeout_seconds": 10,
  "budget_seconds": 20,
  "handlers": {"assistant": "General questions and tasks"}
}
```

Store those environment values in every process that invokes the plugin, or replace
the references with administrator-managed `secret:<id>` catalog credentials.
The proxy value must be an HTTP(S) URL with an explicit port. For direct access use
`network_mode: "direct", proxy_ref: null`; for environment routing use
`network_mode: "environment", proxy_ref: null`.

The API endpoint is `https://api.typesafe.ai`; the console is not the inference API.
The SDK handles retryable failures with up to two additional attempts by default.
The total call budget includes retries and waiting. Authentication and other terminal
SDK errors produce the configured `on_error` action, default `clarify`; cancellation
propagates to the caller. The plugin never silently switches to another provider.

## Bind A Workflow

Publish [workflow.json](workflow.json) as the workflow spec, then create a workflow
instance with an existing, authorized Agent instance in slot `assistant` and:

```json
{
  "tools": {
    "decision": {
      "kind": "plugin",
      "resource_id": "typesafe-decision",
      "allowed_operations": ["decide"],
      "require_approval": false
    }
  }
}
```

Add the `agents.assistant` binding for the actual Agent instance. It must produce a
string result; its own tools, permissions, model provider and network settings belong
to that Agent configuration. A decision handler is a closed label, never a Python
name, tool name, URL or executable expression. To support more handlers, add explicit
branches and bindings to the graph as well as labels to the plugin configuration.

Point a gateway Workflow route at this instance with `output_mode: "message"`.
The sample maps `ignore` to an empty reply, `clarify` to a fixed clarification, and
`dispatch/assistant` to the bound Agent. The gateway supplies current messages and
trusted identity/conversation facts; quoted and recent messages remain untrusted input.
The Agent node uses `input_mode: "messages"` to pass the gateway's message array as
native multimodal Agent input. Other nodes retain the default JSON argument mode.

The supplied configuration has no enterprise domain rules. Customize responsibilities,
labels, graph conditions and application tools in your own plugin/workflow package.
Classification probabilities do not grant platform or tool permissions.

Plugin model calls are recorded as plugin effects. Reported TypeSafe token counts are
returned in the decision result, but are not native OpenAI model cost/quota receipts.
No real API or channel verification is implied by this example.

Official references: [async client](https://docs.typesafe.ai/sdk/python/api/clients/async),
[retries](https://docs.typesafe.ai/sdk/python/api/retries),
[confidence](https://docs.typesafe.ai/confidence).
