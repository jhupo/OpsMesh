# Security policy

## Supported versions

Security fixes target the current source on `master` and the most recent stable release. The `sdk` branch follows the matching SDK release. There is no commitment to patch older releases.

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** action in this repository's Security tab when private vulnerability reporting is enabled. If it is unavailable, contact the maintainer at **hi.jhupo@gmail.com** and request a secure disclosure channel before sending sensitive data. Do not post exploit credentials, private plugin data or database contents in public issues.

Include affected version/commit, affected component, impact, prerequisites and minimal reproduction details. Coordinate disclosure after a fix is available. No response-time guarantee or bug bounty is offered.

## Trust boundaries

Installed Python plugins are trusted executable code. Registration, identity objects and per-user paths do not sandbox arbitrary plugin Python. Restrict plugin installation to administrators and review dependencies. Use the native sandbox backend and deployment-level isolation for untrusted execution; Docker socket access is a privileged capability.

Group membership grants agent use, not workspace access. Caller identity is resolved from stored platform bindings, never gateway role text or caller metadata. Keep HTTPS, database access, encryption keys, bearer tokens and model credentials protected. Preserve the encryption key with backups; losing it makes encrypted state unrecoverable.

Release automation performs CodeQL, dependency auditing, secret/configuration scanning and native/container vulnerability scans, and publishes checksums, SBOMs and provenance. Passing these checks does not establish runtime correctness or prove the absence of vulnerabilities. Verify checksums and provenance before installation. Keep release archives, the data volume and the database under separate access policies.
