# Example Consumer Plugin

This is the smallest business-plugin example for the current framework. It owns
its JSON configuration and does not import a management service or a protocol
implementation directly.

The entry module declares PluginMetadata with ConsumerConfig and settings.json.
The framework validates the file before the Driver starts. Each get_settings()
call reads and validates the consumer's own configuration again.

Use settings.example.json as a template when the plugin needs QQ or DingTalk
connections. The provider plugins must already be installed and
enabled:

~~~json
{
  "dependencies": {
    "qq": {"app_id": "123456", "secret_ref": "QQ_BOT_SECRET"},
    "dingtalk": {"client_id": "configured-client-id", "client_secret_ref": "DINGTALK_CLIENT_SECRET"}
  }
}
~~~

Each dependency value is the provider's config for this consumer. QQ and
DingTalk use it to create a private gateway that delivers events only to this
plugin. Account identifiers must be unique within one protocol.
