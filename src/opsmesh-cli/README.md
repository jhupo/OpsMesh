# OpsMesh Linux service

`opsmesh serve` runs the plugin management service as one systemd process.
Other `opsmesh` commands connect to that process over a local Unix socket.
The service wheel requires the matching framework wheel and does not include
business plugins.

See the [installation and operations guide](https://github.com/jhupo/OpsMesh/blob/master/docs/linux-plugin-service.md)
for the wheelhouse, systemd installer, and plugin management commands.
