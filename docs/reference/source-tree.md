# opsmesh 实际源码与工程文件树

当前 404 个Python源码文件。由`scripts/generate_source_tree.py`生成。排除缓存、构建产物和没有文件的目录。不把预留名称当作已实现功能。

```text
opsmesh/
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.yml
│   │   ├── config.yml
│   │   └── feature_request.yml
│   ├── workflows/
│   │   ├── ci.yml
│   │   └── codeql.yml
│   ├── dependabot.yml
│   └── PULL_REQUEST_TEMPLATE.md
├── deploy/
│   ├── containers/
│   │   └── .gitkeep
│   ├── edge/
│   │   └── .gitkeep
│   ├── install/
│   │   ├── check_ready.py
│   │   ├── common.sh
│   │   ├── docker.sh
│   │   └── local.sh
│   ├── kubernetes/
│   │   ├── job-mode/
│   │   │   ├── api.yaml
│   │   │   ├── coordinator.yaml
│   │   │   ├── kustomization.yaml
│   │   │   ├── observer.yaml
│   │   │   └── rbac.yaml
│   │   ├── .gitkeep
│   │   ├── api.yaml
│   │   ├── configuration.yaml
│   │   ├── kustomization.yaml
│   │   ├── migrate.yaml
│   │   ├── namespace.yaml
│   │   ├── README.md
│   │   ├── storage.yaml
│   │   └── worker.yaml
│   ├── observability/
│   │   └── .gitkeep
│   ├── systemd/
│   │   └── opsmesh.service
│   ├── compose.yaml
│   ├── Dockerfile
│   ├── install.sh
│   ├── linux.md
│   └── README.md
├── docs/
│   ├── adr/
│   │   ├── 0001-modular-monolith.md
│   │   ├── 0002-sdk-boundaries.md
│   │   └── 0003-memory-ownership.md
│   ├── design/
│   │   ├── architecture.md
│   │   ├── bot-adapters.md
│   │   ├── data-and-protocols.md
│   │   ├── memory.md
│   │   ├── overview.md
│   │   ├── plugins.md
│   │   ├── qq-assistant.md
│   │   ├── resource-governance.md
│   │   ├── sdk-and-resources.md
│   │   ├── sdk-coverage.md
│   │   └── source-layout.md
│   ├── reference/
│   │   ├── source-responsibilities.md
│   │   └── source-tree.md
│   ├── releases/
│   │   ├── v0.3.0.md
│   │   ├── v0.3.1.md
│   │   └── v0.3.2.md
│   ├── reviews/
│   │   ├── 2026-11-01-architecture-refactor.md
│   │   ├── 2026-11-01-inbox-cleanup.md
│   │   ├── 2026-11-01-platform-follow-up.md
│   │   ├── 2026-11-01-python-application-api.md
│   │   └── 2026-11-01-source-review.md
│   ├── development-naming.md
│   ├── development.md
│   ├── implementation-report.md
│   ├── initialization-report.md
│   ├── operations.md
│   ├── platform-capability-audit.md
│   ├── platform-completion.md
│   ├── README.md
│   ├── review-fixes.md
│   ├── roadmap.md
│   ├── runtime-api.md
│   └── testing-environment.md
├── examples/
│   ├── custom_plugin/
│   │   ├── opsmesh_example_plugin/
│   │   │   └── __init__.py
│   │   ├── demo.py
│   │   └── README.md
│   ├── qq_community/
│   │   ├── skills/
│   │   │   └── gentle-sister/
│   │   │       ├── agents/
│   │   │       │   └── openai.yaml
│   │   │       ├── references/
│   │   │       │   ├── examples.md
│   │   │       │   ├── golden_quotes.json
│   │   │       │   ├── persona.md
│   │   │       │   └── thinking_framework.md
│   │   │       ├── metadata.json
│   │   │       ├── README.md
│   │   │       ├── SKILL.md
│   │   │       └── UPSTREAM.md
│   │   ├── tools/
│   │   │   ├── __init__.py
│   │   │   └── jev.py
│   │   ├── AGENTS.md
│   │   ├── main.py
│   │   └── README.md
│   └── README.md
├── migrations/
│   ├── 0001-initial.md
│   ├── 0002-runtime-projections.md
│   └── README.md
├── scripts/
│   ├── build_linux_release.py
│   ├── check_linux_installation.py
│   ├── check_repository.py
│   ├── check_sdist.py
│   ├── check_sdk_surface.py
│   └── generate_source_tree.py
├── src/
│   └── opsmesh/
│       ├── application/
│       │   ├── conversations/
│       │   │   ├── __init__.py
│       │   │   ├── activity.py
│       │   │   ├── chat.py
│       │   │   ├── configuration.py
│       │   │   ├── contracts.py
│       │   │   ├── coordinator.py
│       │   │   ├── gateway.py
│       │   │   ├── media.py
│       │   │   ├── moderation.py
│       │   │   ├── operations.py
│       │   │   ├── README.md
│       │   │   ├── reconfiguration.py
│       │   │   ├── service.py
│       │   │   ├── transcript.py
│       │   │   └── validation.py
│       │   ├── __init__.py
│       │   ├── agent_bridge.py
│       │   ├── agent_context.py
│       │   ├── agent_validation.py
│       │   ├── approval_notifications.py
│       │   ├── authorization.py
│       │   ├── coordinator.py
│       │   ├── deployment.py
│       │   ├── memory_bridge.py
│       │   ├── memory_jobs.py
│       │   ├── memory_tools.py
│       │   ├── outbox_delivery.py
│       │   ├── ports.py
│       │   ├── publication.py
│       │   ├── resource_references.py
│       │   ├── run_finalization.py
│       │   ├── run_resources.py
│       │   ├── run_usage.py
│       │   ├── run_workers.py
│       │   ├── runtime_events.py
│       │   ├── sandbox_bridge.py
│       │   ├── schedule_bridge.py
│       │   ├── trigger_bridge.py
│       │   ├── use_cases.py
│       │   └── waiting.py
│       ├── bootstrap/
│       │   ├── __init__.py
│       │   ├── application.py
│       │   ├── bot_adapters.py
│       │   ├── management.py
│       │   ├── services.py
│       │   ├── settings.py
│       │   ├── storage.py
│       │   └── workers.py
│       ├── entrypoints/
│       │   ├── cli/
│       │   │   ├── __init__.py
│       │   │   ├── applications.py
│       │   │   ├── main.py
│       │   │   ├── maintenance.py
│       │   │   └── updates.py
│       │   ├── http/
│       │   │   ├── routes/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── accounts.py
│       │   │   │   ├── agents.py
│       │   │   │   ├── approvals.py
│       │   │   │   ├── bots.py
│       │   │   │   ├── capabilities.py
│       │   │   │   ├── capability_versions.py
│       │   │   │   ├── knowledge.py
│       │   │   │   ├── management.py
│       │   │   │   ├── memory.py
│       │   │   │   ├── models.py
│       │   │   │   ├── notifications.py
│       │   │   │   ├── operations.py
│       │   │   │   ├── personal_models.py
│       │   │   │   ├── resource_sources.py
│       │   │   │   ├── runs.py
│       │   │   │   ├── scheduler.py
│       │   │   │   ├── sessions.py
│       │   │   │   ├── triggers.py
│       │   │   │   ├── updates.py
│       │   │   │   ├── usage.py
│       │   │   │   └── workspaces.py
│       │   │   ├── __init__.py
│       │   │   ├── app.py
│       │   │   ├── security.py
│       │   │   ├── server.py
│       │   │   ├── worker_diagnostics.py
│       │   │   └── workspace_sources.py
│       │   ├── __init__.py
│       │   └── run_worker.py
│       ├── framework/
│       │   ├── adapters/
│       │   │   ├── __init__.py
│       │   │   ├── adapter.py
│       │   │   ├── bot.py
│       │   │   ├── configuration.py
│       │   │   ├── event.py
│       │   │   ├── ingress.py
│       │   │   ├── managed.py
│       │   │   ├── message.py
│       │   │   └── registry.py
│       │   ├── agents/
│       │   │   ├── __init__.py
│       │   │   ├── backend.py
│       │   │   ├── context.py
│       │   │   ├── event.py
│       │   │   ├── hosted.py
│       │   │   ├── model_settings.py
│       │   │   ├── native_tools.py
│       │   │   ├── resources.py
│       │   │   ├── specification.py
│       │   │   └── state.py
│       │   ├── dependencies/
│       │   │   ├── __init__.py
│       │   │   └── utils.py
│       │   ├── drivers/
│       │   │   ├── __init__.py
│       │   │   ├── asgi.py
│       │   │   ├── base.py
│       │   │   ├── httpx.py
│       │   │   ├── models.py
│       │   │   ├── network.py
│       │   │   ├── server.py
│       │   │   └── websockets.py
│       │   ├── internal/
│       │   │   ├── matcher/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── manager.py
│       │   │   │   └── matcher.py
│       │   │   ├── __init__.py
│       │   │   └── event.py
│       │   ├── plugin/
│       │   │   ├── __init__.py
│       │   │   ├── load.py
│       │   │   ├── manager.py
│       │   │   ├── model.py
│       │   │   └── on.py
│       │   ├── __init__.py
│       │   ├── errors.py
│       │   ├── events.py
│       │   ├── exception.py
│       │   ├── matcher.py
│       │   ├── message.py
│       │   ├── params.py
│       │   ├── permission.py
│       │   ├── rule.py
│       │   ├── scheduling.py
│       │   └── typing.py
│       ├── infrastructure/
│       │   ├── bots/
│       │   │   ├── dingtalk/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── adapter.py
│       │   │   │   ├── bot.py
│       │   │   │   ├── client.py
│       │   │   │   ├── configuration.py
│       │   │   │   ├── event.py
│       │   │   │   ├── message.py
│       │   │   │   ├── README.md
│       │   │   │   └── stream.py
│       │   │   ├── qq/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── adapter.py
│       │   │   │   ├── authentication.py
│       │   │   │   ├── bot.py
│       │   │   │   ├── client.py
│       │   │   │   ├── configuration.py
│       │   │   │   ├── event.py
│       │   │   │   ├── gateway.py
│       │   │   │   ├── media.py
│       │   │   │   ├── message.py
│       │   │   │   ├── README.md
│       │   │   │   └── transport.py
│       │   │   └── __init__.py
│       │   ├── coordination/
│       │   │   ├── __init__.py
│       │   │   ├── admission.py
│       │   │   ├── process_lock.py
│       │   │   └── redis.py
│       │   ├── database/
│       │   │   ├── __init__.py
│       │   │   ├── availability.py
│       │   │   ├── connection.py
│       │   │   ├── migrations.py
│       │   │   ├── projections.py
│       │   │   ├── records.py
│       │   │   └── transactions.py
│       │   ├── messaging/
│       │   │   ├── __init__.py
│       │   │   └── outbox.py
│       │   ├── run_workers/
│       │   │   ├── kubernetes/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── client.py
│       │   │   │   ├── configuration.py
│       │   │   │   ├── job_spec.py
│       │   │   │   ├── lifecycle.py
│       │   │   │   └── observation.py
│       │   │   ├── __init__.py
│       │   │   ├── configuration.py
│       │   │   ├── launcher.py
│       │   │   └── status.py
│       │   ├── storage/
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   ├── backup.py
│       │   │   ├── local.py
│       │   │   └── s3.py
│       │   ├── telemetry/
│       │   │   ├── __init__.py
│       │   │   ├── logging.py
│       │   │   ├── metric_store.py
│       │   │   ├── metrics.py
│       │   │   └── tracing.py
│       │   ├── __init__.py
│       │   └── secrets.py
│       ├── maintenance/
│       │   ├── __init__.py
│       │   ├── backup.py
│       │   ├── database.py
│       │   ├── files.py
│       │   ├── project_state.py
│       │   └── updates.py
│       ├── modules/
│       │   ├── accounts/
│       │   │   ├── internal/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── management.py
│       │   │   │   ├── management_base.py
│       │   │   │   ├── management_catalog.py
│       │   │   │   ├── management_credentials.py
│       │   │   │   ├── management_policy.py
│       │   │   │   ├── service.py
│       │   │   │   └── transactions.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   └── README.md
│       │   ├── agents/
│       │   │   ├── internal/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── catalog.py
│       │   │   │   └── factories.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   ├── management.py
│       │   │   └── README.md
│       │   ├── audit/
│       │   │   ├── internal/
│       │   │   │   ├── __init__.py
│       │   │   │   └── journal.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   ├── management.py
│       │   │   ├── operations.py
│       │   │   └── README.md
│       │   ├── bots/
│       │   │   ├── conversations/
│       │   │   │   ├── __init__.py
│       │   │   │   └── api.py
│       │   │   ├── internal/
│       │   │   │   ├── conversations/
│       │   │   │   │   ├── __init__.py
│       │   │   │   │   ├── batches.py
│       │   │   │   │   ├── inbox.py
│       │   │   │   │   ├── state.py
│       │   │   │   │   └── storage.py
│       │   │   │   ├── __init__.py
│       │   │   │   ├── configuration_store.py
│       │   │   │   ├── delivery.py
│       │   │   │   ├── delivery_outcomes.py
│       │   │   │   ├── delivery_records.py
│       │   │   │   ├── ingress.py
│       │   │   │   ├── management.py
│       │   │   │   ├── record_keys.py
│       │   │   │   └── service.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   └── README.md
│       │   ├── capabilities/
│       │   │   ├── internal/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── availability_management.py
│       │   │   │   ├── catalog.py
│       │   │   │   ├── code_tools.py
│       │   │   │   ├── embeddings.py
│       │   │   │   ├── git_skills.py
│       │   │   │   ├── knowledge.py
│       │   │   │   ├── knowledge_jobs.py
│       │   │   │   ├── knowledge_management.py
│       │   │   │   ├── mcp.py
│       │   │   │   ├── resource_compiler.py
│       │   │   │   ├── scripts.py
│       │   │   │   ├── serialization.py
│       │   │   │   ├── skills.py
│       │   │   │   └── tool_invoker.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   ├── embedding_registry.py
│       │   │   ├── events.py
│       │   │   ├── knowledge.py
│       │   │   ├── knowledge_management.py
│       │   │   ├── management.py
│       │   │   ├── mcp_management.py
│       │   │   ├── README.md
│       │   │   ├── script_management.py
│       │   │   └── skill_management.py
│       │   ├── configuration/
│       │   │   ├── internal/
│       │   │   │   ├── __init__.py
│       │   │   │   └── service.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   ├── management.py
│       │   │   └── README.md
│       │   ├── execution/
│       │   │   ├── internal/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── docker.py
│       │   │   │   ├── processes.py
│       │   │   │   ├── service.py
│       │   │   │   └── session.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   └── README.md
│       │   ├── memory/
│       │   │   ├── internal/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── generation.py
│       │   │   │   ├── requests.py
│       │   │   │   ├── retention.py
│       │   │   │   └── service.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   ├── events.py
│       │   │   ├── management.py
│       │   │   └── README.md
│       │   ├── models/
│       │   │   ├── internal/
│       │   │   │   ├── __init__.py
│       │   │   │   └── service.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   ├── credentials.py
│       │   │   ├── management.py
│       │   │   ├── README.md
│       │   │   └── validation.py
│       │   ├── notifications/
│       │   │   ├── internal/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── delivery_state.py
│       │   │   │   └── service.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   ├── management.py
│       │   │   └── README.md
│       │   ├── operations/
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   ├── outbox.py
│       │   │   └── plugins.py
│       │   ├── runtime/
│       │   │   ├── internal/
│       │   │   │   ├── backends/
│       │   │   │   │   ├── openai_agents/
│       │   │   │   │   │   ├── __init__.py
│       │   │   │   │   │   ├── agent_builder.py
│       │   │   │   │   │   ├── backend.py
│       │   │   │   │   │   ├── compaction.py
│       │   │   │   │   │   ├── computer.py
│       │   │   │   │   │   ├── config.py
│       │   │   │   │   │   ├── delegation.py
│       │   │   │   │   │   ├── errors.py
│       │   │   │   │   │   ├── execution_state.py
│       │   │   │   │   │   ├── extension_mapping.py
│       │   │   │   │   │   ├── guardrails.py
│       │   │   │   │   │   ├── history.py
│       │   │   │   │   │   ├── hooks.py
│       │   │   │   │   │   ├── hosted_mcp.py
│       │   │   │   │   │   ├── hosted_tools.py
│       │   │   │   │   │   ├── mcp_config.py
│       │   │   │   │   │   ├── model_validation.py
│       │   │   │   │   │   ├── models.py
│       │   │   │   │   │   ├── native_tools.py
│       │   │   │   │   │   ├── output.py
│       │   │   │   │   │   ├── sandbox.py
│       │   │   │   │   │   ├── sandbox_delegation.py
│       │   │   │   │   │   ├── sandbox_docker.py
│       │   │   │   │   │   ├── sandbox_lifecycle.py
│       │   │   │   │   │   ├── sandbox_memory.py
│       │   │   │   │   │   ├── sandbox_remote_docker.py
│       │   │   │   │   │   ├── serialization.py
│       │   │   │   │   │   ├── state.py
│       │   │   │   │   │   ├── streaming.py
│       │   │   │   │   │   ├── tool_selection.py
│       │   │   │   │   │   ├── tools.py
│       │   │   │   │   │   ├── tracing.py
│       │   │   │   │   │   └── usage.py
│       │   │   │   │   └── __init__.py
│       │   │   │   ├── __init__.py
│       │   │   │   ├── approvals.py
│       │   │   │   ├── artifacts.py
│       │   │   │   ├── cleanup.py
│       │   │   │   ├── extensions.py
│       │   │   │   ├── history.py
│       │   │   │   ├── management_base.py
│       │   │   │   ├── metrics.py
│       │   │   │   ├── records.py
│       │   │   │   ├── service.py
│       │   │   │   ├── session_recovery.py
│       │   │   │   ├── validation.py
│       │   │   │   └── workers.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   ├── approval_contracts.py
│       │   │   ├── approval_management.py
│       │   │   ├── events.py
│       │   │   ├── extension_contracts.py
│       │   │   ├── management.py
│       │   │   ├── openai_extensions.py
│       │   │   ├── overrides.py
│       │   │   ├── prompts.py
│       │   │   ├── queries.py
│       │   │   └── README.md
│       │   ├── scheduling/
│       │   │   ├── internal/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── calendar.py
│       │   │   │   ├── engine.py
│       │   │   │   ├── repository.py
│       │   │   │   └── service.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   ├── events.py
│       │   │   └── README.md
│       │   ├── sessions/
│       │   │   ├── internal/
│       │   │   │   ├── __init__.py
│       │   │   │   └── service.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   ├── management.py
│       │   │   └── README.md
│       │   ├── triggers/
│       │   │   ├── internal/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── delivery.py
│       │   │   │   ├── matching.py
│       │   │   │   ├── service.py
│       │   │   │   └── webhook.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   ├── events.py
│       │   │   └── README.md
│       │   ├── usage/
│       │   │   ├── internal/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── budgets.py
│       │   │   │   ├── ledger.py
│       │   │   │   ├── observations.py
│       │   │   │   ├── pricing.py
│       │   │   │   ├── reconciliation.py
│       │   │   │   ├── resource_pricing.py
│       │   │   │   └── service.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   ├── management.py
│       │   │   └── README.md
│       │   ├── workspaces/
│       │   │   ├── internal/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── files.py
│       │   │   │   ├── garbage_collection.py
│       │   │   │   ├── locking.py
│       │   │   │   ├── paths.py
│       │   │   │   ├── restoration.py
│       │   │   │   ├── service.py
│       │   │   │   └── snapshots.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   ├── events.py
│       │   │   ├── management.py
│       │   │   └── README.md
│       │   ├── __init__.py
│       │   └── resource_contracts.py
│       ├── __init__.py
│       ├── __main__.py
│       ├── _version.py
│       ├── dependencies.py
│       └── py.typed
├── tests/
│   ├── accounts/
│   │   ├── test_management.py
│   │   └── test_permission_actions.py
│   ├── adapters/
│   │   ├── test_dingtalk.py
│   │   ├── test_qq.py
│   │   ├── test_qq_gateway.py
│   │   └── test_qq_media.py
│   ├── application/
│   │   ├── test_conversation_activity.py
│   │   ├── test_conversation_agent_validation.py
│   │   ├── test_conversation_coordinator.py
│   │   ├── test_conversation_media.py
│   │   ├── test_conversation_operations.py
│   │   ├── test_conversation_reconfiguration.py
│   │   ├── test_conversation_service.py
│   │   ├── test_conversation_transcript.py
│   │   ├── test_conversation_workers.py
│   │   └── test_run_waiting.py
│   ├── bots/
│   │   ├── fixtures.py
│   │   ├── helpers.py
│   │   ├── test_adapter_registry.py
│   │   ├── test_authorization_boundaries.py
│   │   ├── test_bindings.py
│   │   ├── test_conversation_inbox.py
│   │   ├── test_conversation_queue_recovery.py
│   │   ├── test_delivery_outcomes.py
│   │   ├── test_management.py
│   │   └── test_unlinked_group_identity.py
│   ├── deployment/
│   │   ├── conftest.py
│   │   ├── test_installation.py
│   │   └── test_linux_release.py
│   ├── examples/
│   │   ├── conftest.py
│   │   ├── test_qq_community_application.py
│   │   └── test_qq_community_moderation.py
│   ├── framework/
│   │   ├── conftest.py
│   │   ├── test_dependencies.py
│   │   ├── test_events_matchers.py
│   │   ├── test_example_plugin.py
│   │   ├── test_managed_adapter_policy.py
│   │   ├── test_network_driver.py
│   │   ├── test_persistent_scheduler.py
│   │   ├── test_plugin_platform_ownership.py
│   │   ├── test_plugins_driver.py
│   │   └── test_server_driver.py
│   ├── http/
│   │   ├── conftest.py
│   │   ├── test_accounts_management.py
│   │   ├── test_api.py
│   │   ├── test_approvals_management.py
│   │   ├── test_bot_authorization.py
│   │   ├── test_bot_bindings.py
│   │   ├── test_driver_ingress.py
│   │   ├── test_hosted_mcp_security.py
│   │   ├── test_lifecycle.py
│   │   ├── test_management_boundaries.py
│   │   ├── test_model_profile_validation.py
│   │   ├── test_notifications_operations.py
│   │   ├── test_official_bot_adapters.py
│   │   ├── test_personal_models.py
│   │   ├── test_resource_extensions.py
│   │   ├── test_resource_management.py
│   │   ├── test_run_overrides.py
│   │   ├── test_submission_projection.py
│   │   └── test_worker_diagnostics.py
│   ├── infrastructure/
│   │   ├── postgresql_service.py
│   │   ├── s3_protocol.py
│   │   ├── test_admission.py
│   │   ├── test_blob_storage.py
│   │   ├── test_cache.py
│   │   ├── test_conversation_inbox_postgresql.py
│   │   ├── test_database.py
│   │   ├── test_metric_store.py
│   │   ├── test_notification_postgresql.py
│   │   ├── test_package_imports.py
│   │   ├── test_platform_backup.py
│   │   ├── test_postgresql.py
│   │   ├── test_postgresql_bot_authorization.py
│   │   ├── test_postgresql_service.py
│   │   ├── test_project_state_backup.py
│   │   ├── test_release_updates.py
│   │   ├── test_s3_integration.py
│   │   ├── test_s3_storage.py
│   │   └── test_telemetry_export.py
│   ├── notifications/
│   │   └── test_notifications.py
│   ├── operations/
│   │   └── test_management.py
│   ├── platform/
│   │   ├── test_agent_bindings.py
│   │   ├── test_application.py
│   │   ├── test_bot_bindings.py
│   │   ├── test_live_memory.py
│   │   ├── test_live_memory_resume.py
│   │   ├── test_live_model.py
│   │   ├── test_memory_bridge.py
│   │   ├── test_python_application_api.py
│   │   ├── test_scheduling.py
│   │   └── test_services.py
│   ├── resources/
│   │   ├── test_application_deployment.py
│   │   ├── test_content_gc.py
│   │   ├── test_docker_integration.py
│   │   ├── test_git_skills.py
│   │   ├── test_knowledge_management.py
│   │   ├── test_memory_lifecycle.py
│   │   ├── test_memory_management.py
│   │   ├── test_resource_management.py
│   │   ├── test_resource_recovery.py
│   │   ├── test_resources.py
│   │   ├── test_retrieval_mcp.py
│   │   ├── test_script_tools.py
│   │   ├── test_skill_resource_paths.py
│   │   ├── test_workspace_concurrency.py
│   │   └── test_workspace_management.py
│   ├── runtime/
│   │   ├── fixtures/
│   │   │   └── kubernetes_probe.py
│   │   ├── test_advanced_extensions.py
│   │   ├── test_compaction_and_websocket.py
│   │   ├── test_execution_metadata.py
│   │   ├── test_extensions.py
│   │   ├── test_hosted_mcp.py
│   │   ├── test_hosted_tools.py
│   │   ├── test_indexed_records.py
│   │   ├── test_isolated_sandbox.py
│   │   ├── test_kubernetes_integration.py
│   │   ├── test_kubernetes_workers.py
│   │   ├── test_management.py
│   │   ├── test_native_tools.py
│   │   ├── test_openai_backend.py
│   │   ├── test_provider_errors.py
│   │   ├── test_remote_docker.py
│   │   ├── test_run_launcher.py
│   │   ├── test_run_overrides.py
│   │   ├── test_runtime.py
│   │   ├── test_sandbox_bridge.py
│   │   ├── test_sandbox_configuration.py
│   │   ├── test_sandbox_docker_limits.py
│   │   ├── test_sandbox_memory.py
│   │   ├── test_sandbox_providers.py
│   │   ├── test_sandbox_sources.py
│   │   ├── test_session_recovery.py
│   │   ├── test_text_runner_features.py
│   │   ├── test_tracing.py
│   │   └── test_usage_observation.py
│   ├── scheduling/
│   │   ├── test_calendar.py
│   │   ├── test_concurrency.py
│   │   ├── test_http.py
│   │   ├── test_materialization_recovery.py
│   │   └── test_service.py
│   ├── triggers/
│   │   ├── test_http.py
│   │   ├── test_recovery_review.py
│   │   ├── test_submission_acknowledgment.py
│   │   └── test_triggers.py
│   ├── usage/
│   │   ├── test_resource_billing.py
│   │   └── test_usage.py
│   ├── conftest.py
│   ├── test_distribution.py
│   └── test_import_boundaries.py
├── web/
│   ├── console/
│   │   └── .gitkeep
│   ├── portal/
│   │   └── .gitkeep
│   └── README.md
├── .dockerignore
├── .editorconfig
├── .env.example
├── .gitattributes
├── .gitignore
├── .pre-commit-config.yaml
├── .python-version
├── AGENTS.md
├── CHANGELOG.md
├── CODE_OF_CONDUCT.md
├── CONTRIBUTING.md
├── LICENSE
├── pyproject.toml
├── README.en.md
├── README.md
├── SECURITY.md
├── SUPPORT.md
├── THIRD_PARTY_NOTICES.md
└── uv.lock
```
