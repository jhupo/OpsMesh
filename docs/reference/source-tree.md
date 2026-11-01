# opsmesh 实际源码与工程文件树

当前 366 个Python源码文件。由`scripts/generate_source_tree.py`生成。排除缓存、构建产物和没有文件的目录。不把预留名称当作已实现功能。

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
│   │   ├── resource-governance.md
│   │   ├── sdk-and-resources.md
│   │   ├── sdk-coverage.md
│   │   └── source-layout.md
│   ├── reference/
│   │   ├── source-responsibilities.md
│   │   └── source-tree.md
│   ├── releases/
│   │   └── v0.3.0.md
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
│   └── README.md
├── migrations/
│   ├── 0001-initial.md
│   └── README.md
├── scripts/
│   ├── check_linux_installation.py
│   ├── check_repository.py
│   ├── check_sdist.py
│   ├── check_sdk_surface.py
│   └── generate_source_tree.py
├── src/
│   └── opsmesh/
│       ├── application/
│       │   ├── __init__.py
│       │   ├── agent_bridge.py
│       │   ├── agent_context.py
│       │   ├── agent_validation.py
│       │   ├── authorization.py
│       │   ├── coordinator.py
│       │   ├── memory_bridge.py
│       │   ├── memory_tools.py
│       │   ├── ports.py
│       │   ├── resource_references.py
│       │   ├── run_resources.py
│       │   ├── run_usage.py
│       │   ├── sandbox_bridge.py
│       │   ├── schedule_bridge.py
│       │   ├── trigger_bridge.py
│       │   └── use_cases.py
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
│       │   │   ├── main.py
│       │   │   └── maintenance.py
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
│       │   │   │   ├── resource_sources.py
│       │   │   │   ├── runs.py
│       │   │   │   ├── scheduler.py
│       │   │   │   ├── sessions.py
│       │   │   │   ├── triggers.py
│       │   │   │   ├── usage.py
│       │   │   │   └── workspaces.py
│       │   │   ├── __init__.py
│       │   │   ├── app.py
│       │   │   ├── security.py
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
│       │   │   ├── onebot/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── adapter.py
│       │   │   │   ├── bot.py
│       │   │   │   ├── configuration.py
│       │   │   │   ├── event.py
│       │   │   │   ├── message.py
│       │   │   │   ├── README.md
│       │   │   │   ├── response.py
│       │   │   │   └── websocket.py
│       │   │   ├── qq/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── adapter.py
│       │   │   │   ├── authentication.py
│       │   │   │   ├── bot.py
│       │   │   │   ├── client.py
│       │   │   │   ├── configuration.py
│       │   │   │   ├── event.py
│       │   │   │   ├── message.py
│       │   │   │   └── README.md
│       │   │   └── __init__.py
│       │   ├── coordination/
│       │   │   ├── __init__.py
│       │   │   ├── admission.py
│       │   │   └── redis.py
│       │   ├── database/
│       │   │   ├── __init__.py
│       │   │   ├── availability.py
│       │   │   ├── connection.py
│       │   │   ├── migrations.py
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
│       │   └── files.py
│       ├── modules/
│       │   ├── accounts/
│       │   │   ├── internal/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── management.py
│       │   │   │   ├── management_base.py
│       │   │   │   ├── management_catalog.py
│       │   │   │   ├── management_credentials.py
│       │   │   │   ├── management_policy.py
│       │   │   │   ├── permission_actions.py
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
│       │   │   ├── internal/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── account_migration.py
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
│       │   │   │   ├── service.py
│       │   │   │   └── validation.py
│       │   │   ├── __init__.py
│       │   │   ├── api.py
│       │   │   ├── management.py
│       │   │   └── README.md
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
│       │   │   │   │   │   ├── extension_mapping.py
│       │   │   │   │   │   ├── guardrails.py
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
│       │   │   │   ├── artifacts.py
│       │   │   │   ├── cleanup.py
│       │   │   │   ├── extensions.py
│       │   │   │   ├── history.py
│       │   │   │   ├── management_base.py
│       │   │   │   ├── metrics.py
│       │   │   │   ├── service.py
│       │   │   │   ├── session_recovery.py
│       │   │   │   └── validation.py
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
│       ├── plugins/
│       │   ├── scheduler/
│       │   │   ├── __init__.py
│       │   │   └── persistent.py
│       │   └── __init__.py
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
│   │   └── test_qq.py
│   ├── bots/
│   │   ├── helpers.py
│   │   ├── test_adapter_registry.py
│   │   ├── test_authorization_boundaries.py
│   │   ├── test_bindings.py
│   │   ├── test_delivery_outcomes.py
│   │   ├── test_management.py
│   │   ├── test_onebot.py
│   │   └── test_onebot_network.py
│   ├── deployment/
│   │   └── test_installation.py
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
│   │   ├── test_notifications_operations.py
│   │   ├── test_official_bot_adapters.py
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
│   │   ├── test_database.py
│   │   ├── test_metric_store.py
│   │   ├── test_notification_postgresql.py
│   │   ├── test_package_imports.py
│   │   ├── test_platform_backup.py
│   │   ├── test_postgresql.py
│   │   ├── test_postgresql_bot_authorization.py
│   │   ├── test_postgresql_service.py
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
│   │   ├── test_scheduling.py
│   │   └── test_services.py
│   ├── resources/
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
