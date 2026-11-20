# Group assistants

> **Migration note:** This README describes the pre-restoration implementation. Its commands and examples do not target the restored NoneBot2 core. See the [repository overview](../../README.md) for current interfaces and migration status.

This text-only example owns two native SDK agents and one robot connection.
`handlers.py` uses native framework Rules to send group A to the sales agent
and group B to the technical agent. Private messages and unmatched groups do
not invoke a model. Replace the placeholder group IDs in that file with IDs
received from the platform. There is no route table in configuration.

This example requires the channels and execution plugins, model credentials,
and `DINGTALK_CLIENT_SECRET` in the process environment. The example's
`group_assistant/settings.json` documents its configuration shape.
The plugin declares its robot at
`dependencies.channels.dingtalk.main`.
Channels validates the protocol settings and opens the named robot after the
plugin becomes ready. No channel fields are defined in the plugin's Settings.
For QQ, use `dependencies.channels.qq.main` with `app_id` and `secret_ref`;
its group identifiers are QQ group openids.
`in_group()` uses protocol-specific event data.

These handlers run independent turns without conversation history. A plugin
requiring continuity supplies its own native SDK Session, keyed by connection,
group and agent (and user when conversations are private to each member).
See the chat example for persistent sessions and session commands.

Use `protocol="qq"` or `protocol="dingtalk"` in `in_group()` when both
protocols have a connection with the same name. Rules remain owned by the
declaring plugin. Overlapping custom Rules follow the framework's existing
matcher priority and blocking behavior.
