# Security Policy

OpsMesh handles model provider credentials, MCP credentials, workspace files, runtime execution, and audit records. Please report security issues privately.

## Reporting A Vulnerability

Open a private security advisory on GitHub, or contact the maintainers through the repository owner if advisories are unavailable. Do not create a public issue for vulnerabilities.

Please include:

- affected version or commit
- affected component
- reproduction steps
- expected impact
- any logs or payloads with secrets redacted

## Supported Versions

The project is pre-1.0. Security fixes target the default branch unless a maintained release branch is announced.

## Security Scope

High-priority reports include:

- cross-workspace data access
- credential leakage
- sandbox escape or host command execution
- public marketplace review bypass
- webhook signature weakness
- audit log tampering
- privilege escalation in approval or admin APIs

