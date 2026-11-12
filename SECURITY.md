# Security policy

## Supported scope

OpsMesh is in early development and does not yet offer a long-term security support or backport commitment. Use the newest reviewed release and its matching official plugin packages. A framework upgrade does not automatically update external plugins.

## Reporting a vulnerability

If the published repository has GitHub private vulnerability reporting enabled, use **Security → Report a vulnerability**. Otherwise, request a private contact channel from the repository maintainer without posting exploit details, secrets or affected customer data in a public issue. This repository does not yet publish a dedicated security email or response-time guarantee.

Include affected versions and packages, the impact, minimal reproduction steps and suggested mitigation. Redact credentials and personal data. For a vulnerability in an upstream library, identify that library and version as well.

## Trust boundaries

- Python plugins run inside the host process with the service account's permissions. Loading admission and plugin-owned paths are not a sandbox for hostile Python code.
- Only administrators should deploy plugin source, migration scripts or dependency directories. Do not load untrusted uploads directly as plugins.
- API credentials and bot signature checks authenticate channel requests. Business plugins remain responsible for resource authorization and credential separation.
- Local sandbox workspace isolation is not equivalent to operating-system or container isolation.
- Trace metadata may contain application data. Keep secrets out of logs and metadata, and configure sensitive input/output collection deliberately.
- Systemd hardening and filesystem path checks reduce accidental access; they do not replace host isolation and least-privilege deployment.

Release checksums establish file integrity. Verify release provenance against the intended repository when accepting binaries. Plugin packages and their transitive dependencies are part of the application's supply chain.
