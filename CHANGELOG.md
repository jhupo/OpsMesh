# Changelog

## Unreleased

No unreleased changes.

All notable changes are recorded here. Versions follow Semantic Versioning. Dates use UTC. This file describes planned release contents; a section alone does not mean the release has been published.

## [0.2.0] - 2026-10-25

### Added

- Plugin application host with immutable publication dependencies and native Agents SDK integration.
- Persistent tasks, checkpoints, human input, scheduling, session isolation, caller token accounting and private resource management.
- QQ integration with Tencent's official `qq-botpy` SDK and an OpsMesh-owned Adapter contract shared directly by QQ and DingTalk.
- DingTalk native Stream lifecycle, robot/event/card/Graph callbacks, proactive messages, media and fixed-host OpenAPI operations.
- Encrypted gateway events, administrator operation queues, delivery receipts and explicit retry for uncertain effects.
- Repository skills for OpenAI Agents, QQ and DingTalk maintenance.
- Authenticated SSE task events, encrypted event storage and cursor-based replay after reconnects.
- Standalone typed Python plugin SDK with a packaging CLI, independent development configuration and examples.
- Linux x86_64/aarch64 executable bundles, systemd installation, optional updates and multi-platform GHCR images.
- Tag release automation for server/SDK Python distributions, source tar/ZIP archives, installer, checksums, SBOMs, scan reports and build provenance.
- Standard contribution/security policies, issue templates and dependency maintenance.

### Changed

- Simplified the host around native SDK contracts; orchestration belongs to plugin code.
- Isolated QQ shard sessions and audit waiters; acknowledge callbacks and advance resume cursors only after durable event persistence.
- Defined public extension contracts in the framework and generated SDK runtime bindings and standalone typing stubs from those definitions in a separately staged SDK project.
- Removed redundant adapter layering, local platform model trees, webhook transport and framework copies; aligned documentation and release/security tooling.
- Pin QQ's metadata-only source fix and cryptography >=50.0.2 instead of retaining the upstream vulnerable dependency cap.
- Unified project licensing under MIT. Redistributed third-party code retains its own notices.
- The `sdk` branch presents the independent SDK at its branch root.
- Generated administrator credentials are handed off in owner-only files; process logs print only their location. Remove the file after rotating the initial password.
- Installed environment secrets are readable only by the service account and root, rather than the service group.
- Preserve Python and GitHub Actions security-extended CodeQL configurations; release builds depend on both analyses.

### Deployment

Initialize a new installation with its own database, data directory and encryption key. Complete or cancel pending work before upgrading. Restoring checkpoints requires the pinned SDK and plugin versions. Production distributions support Linux only.
