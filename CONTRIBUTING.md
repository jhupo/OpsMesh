# Contributing

SDK development is maintained in `sdk/python` of [OpsMesh's main tree](https://github.com/jhupo/OpsMesh). The `sdk` branch is an independently installable snapshot; direct changes there need to be brought back to the main tree before the next release. Open issues and pull requests in that repository.

Use Python 3.11.8+ and follow [AGENTS.md](AGENTS.md). Install development tools with `uv sync --extra dev --extra agents`, run the documented static checks, and build with `uv build`. The SDK must remain independent from host implementation imports. Include examples and README changes for public contract changes.

Automated contributors run tests, model calls and services only when explicitly requested. Report checks actually performed and any runtime gaps. Do not commit credentials, generated environments, plugin state or private files.

The SDK and host share a release version. Their wheels and source distributions are built from the same tagged host commit and attached to its GitHub Release; PyPI publication is not configured. Contributions use LGPL-3.0-only. For vulnerabilities follow [SECURITY.md](SECURITY.md).
