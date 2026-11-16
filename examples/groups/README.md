# Group assistants

This text-only example owns two native SDK agents and one robot connection.
`handlers.py` uses native framework Rules to send group A to the sales agent
and group B to the technical agent. Private messages and unmatched groups do
not invoke a model. Replace the placeholder group IDs in that file with IDs
received from the platform. There is no route table in configuration.

Install the channels and execution plugins, then register `group_assistant`
with this directory as its import root. Set the SDK model credentials and
`DINGTALK_CLIENT_SECRET` in the service environment. Fill in
`group_assistant/settings.json` before the first activation. Metadata declares
this file, so the host validates and saves it automatically on first startup.
Register the plugin without `--config` to use its declared file.

After initialization, saved SQLite values take precedence. To explicitly import
an edited file while the host is stopped:

```sh
opsmesh --manifest /etc/opsmesh/host.json --plugin-action import-config \
  --module group_assistant
```

Enable the plugin after registration. Channels validates `dependencies.channels`
and opens the named robot after the plugin becomes ready. No channel fields
are defined in this plugin's Settings and no connection function is called.
The same code works with QQ by replacing `dingtalk.main` with `qq.main` using
`app_id` and `secret_ref`; its group identifiers are QQ group openids.

These handlers run independent turns without conversation history. A plugin
requiring continuity supplies its own native SDK Session, keyed by connection,
group and agent (and user when conversations are private to each member).
See the chat example for persistent sessions and session commands.

Use `protocol="qq"` or `protocol="dingtalk"` in `in_group()` when both
protocols have a connection with the same name. Rules remain owned by the
declaring plugin. Overlapping custom Rules follow the framework's existing
matcher priority and blocking behavior.
