# OpsMesh Service

`opsmesh-service` provides the `opsmesh` command, the host process, local
administration, and the SQLite plugin catalog. It depends on the `opsmesh`
framework, but is not itself a plugin. Install business plugins separately.

Run `opsmesh --manifest /path/to/host.json` to start the service. Use
`opsmesh --manifest /path/to/host.json --check` to validate the host configuration
and plugin declarations without starting plugin resources.
