# Security policy

Security fixes target the current SDK matching the latest supported OpsMesh release.

Report vulnerabilities through the repository's private GitHub vulnerability reporting when available, or contact **hi.jhupo@gmail.com** to arrange secure disclosure. Never send credentials, private plugin data or exploit details in a public issue. Include affected version and impact; no response-time guarantee or bounty is offered.

Installed plugins execute trusted Python. Identity protocols and path contracts are not an OS isolation boundary. Review dependencies, use native sandbox capabilities for untrusted workloads and preserve host permission/accounting contracts. Do not derive platform privileges from metadata or external group roles.
