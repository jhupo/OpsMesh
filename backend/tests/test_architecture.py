"""Exercise the adopted linter against actual source, including intentional violations."""

import ast
import importlib.util
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_consolidated_domains_have_one_source_owner() -> None:
    app = ROOT / "backend/app"
    assert {
        path.name
        for path in app.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    } == {
        "api",
        "core",
        "domains",
        "observability",
        "runtime",
    }
    assert {path.name for path in app.glob("*.py")} == {"__init__.py", "delivery.py", "main.py"}
    for name in (
        "core/admin",
        "core/auth",
        "core/common",
        "core/db",
        "core/identity",
        "core/integrations",
        "core/rate_limits",
        "core/redis",
        "core/secrets",
        "core/security",
        "domains/agents",
        "domains/capabilities",
        "domains/orchestration",
        "domains/workspace",
        "runtime/operations",
        "runtime/self_hosted",
        "runtime/workers",
        "runtime/environment",
        "observability",
    ):
        assert (app / name / "__init__.py").is_file(), name
    for name in (
        "agents",
        "capabilities",
        "orchestration",
        "workspace",
        "platform",
        "execution",
        "runtime_manager",
        "core/platform",
    ):
        target = app / name
        assert not target.exists(), name


@pytest.mark.parametrize(
    "module",
    [
        "backend.app.runtime.operations.workers.lifecycle",
        "backend.app.runtime.operations.recovery.service",
        "backend.app.domains.workspace.teams.execution_loop",
    ],
)
def test_consolidated_services_import_in_a_fresh_process(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import importlib; importlib.import_module({module!r})"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_team_consolidation_removes_superseded_sources() -> None:
    teams = ROOT / "backend/app/domains/workspace/teams"
    for name in (
        "execution_loop_constants.py",
        "execution_loop_jobs.py",
        "execution_loop_recorder.py",
        "execution_loop_repository.py",
        "execution_overview_members.py",
        "command_center_constants.py",
        "project_dashboard_constants.py",
        "provider_readiness.py",
        "provider_readiness_constants.py",
    ):
        assert not (teams / name).exists(), name

    app = ROOT / "backend/app"
    for name in (
        "domains/agents/messages/pagination.py",
        "domains/orchestration/tasks/control_payloads.py",
        "runtime/workers/job_handlers/base.py",
        "domains/workspace/teams/operating_context.py",
        "api/services/workspace_export_constants.py",
        "api/schemas/redaction.py",
    ):
        assert not (app / name).exists(), name


def test_team_features_are_nested_by_function() -> None:
    teams = ROOT / "backend/app/domains/workspace/teams"
    expected = {
        "execution",
        "operations",
        "projects",
        "providers",
        "organization",
        "runtime",
    }
    assert {path.name for path in teams.iterdir() if path.is_dir()} >= expected
    root_modules = {
        path.stem
        for path in teams.glob("*.py")
        if path.name != "__init__.py"
    }
    assert root_modules <= {
        "models",
        "command_center",
        "execution_loop",
        "execution_overview",
        "operating_context_service",
        "operations_console",
        "project_service",
        "provider_readiness_service",
        "runtime",
        "workspace_command_center",
        "workspace_service",
    }
    assert (teams / "runtime/service.py").is_file()
    assert (teams / "operations/console_normalization.py").is_file()
    for name in (
        "runtime/service_binding.py",
        "runtime/service_heartbeat.py",
        "runtime/service_lifecycle.py",
        "operations/command_center_utils.py",
        "organization/policy_visibility.py",
        "operations/console_utils.py",
    ):
        assert not (teams / name).exists(), name


def test_runtime_features_are_nested_by_function() -> None:
    runtime = ROOT / "backend/app/runtime/environment"
    expected = {
        "backends",
        "commands",
        "lifecycle",
        "pool",
        "policies",
        "spaces",
    }
    assert {path.name for path in runtime.iterdir() if path.is_dir()} >= expected
    root_modules = {
        path.stem
        for path in runtime.glob("*.py")
        if path.name != "__init__.py"
    }
    assert root_modules <= {
        "contracts",
        "dependencies",
        "manager",
        "manager_factory",
        "metadata",
        "models",
        "project_files",
        "provisioning",
        "provisioning_executor",
        "queries",
        "run_environment",
            "service",
            "security_events",
            "url_fetch",
    }
    for name in (
        "backend_registry.py",
        "command_executor.py",
        "command_output.py",
        "commands.py",
        "docker_client.py",
        "egress.py",
        "events.py",
        "lifecycle_cleanup.py",
        "lifecycle_control.py",
        "lifecycle_guards.py",
        "pool_leases.py",
        "quotas.py",
        "runtime_policy.py",
        "safety.py",
        "space_models.py",
        "space_service.py",
        "spaces/helpers.py",
    ):
        assert not (runtime / name).exists(), name


def test_task_modules_are_nested_by_function() -> None:
    tasks = ROOT / "backend/app/domains/orchestration/tasks"
    expected = {
        "collaboration",
        "control",
        "delivery",
        "execution",
        "management",
        "observation",
        "operations",
    }
    assert {path.name for path in tasks.iterdir() if path.is_dir()} >= expected
    root_modules = {
        path.stem
        for path in tasks.glob("*.py")
        if path.name != "__init__.py"
    }
    assert root_modules <= {
        "events",
        "event_outbox",
        "feedback",
        "message_append",
        "models",
        "service",
        "status",
        "step_service",
        "step_status",
    }
    for name in (
        "control.py",
        "control_diagnostics.py",
        "manager_contracts.py",
        "observation.py",
        "observation_utils.py",
        "operator_actions.py",
        "workspace_service.py",
    ):
        assert not (tasks / name).exists(), name


def test_workflow_modules_are_nested_by_function() -> None:
    workflows = ROOT / "backend/app/domains/orchestration/workflows"
    expected = {"definitions", "planning", "scheduling", "steps"}
    assert {path.name for path in workflows.iterdir() if path.is_dir()} >= expected
    root_modules = {
        path.stem
        for path in workflows.glob("*.py")
        if path.name != "__init__.py"
    }
    assert root_modules == {"statuses"}
    assert (workflows / "planning/project_plan/__init__.py").is_file()
    for name in (
        "blocked_reasons.py",
        "conditions.py",
        "data.py",
        "definition_commands.py",
        "definitions.py",
        "plan_agent_plan.py",
        "plan_attempts.py",
        "plan_diagnostics.py",
        "plan_project_plan_members.py",
        "plan_workflow_contracts.py",
        "planning_completion.py",
        "scheduler_main.py",
        "step_launcher.py",
        "subworkflows.py",
    ):
        assert not (workflows / name).exists(), name


def test_tenant_modules_are_nested_by_function() -> None:
    tenants = ROOT / "backend/app/domains/workspace/tenants"
    expected = {"health", "lifecycle"}
    assert {path.name for path in tenants.iterdir() if path.is_dir()} >= expected
    root_modules = {
        path.stem
        for path in tenants.glob("*.py")
        if path.name != "__init__.py"
    }
    assert root_modules == {"models", "quotas"}
    for name in (
        "lifecycle/actions",
        "lifecycle/diagnostics",
        "lifecycle/scheduling",
    ):
        assert (tenants / name / "__init__.py").is_file(), name
    for name in (
        "data_lifecycle.py",
        "data_lifecycle_actions.py",
        "data_lifecycle_diagnostics.py",
        "data_lifecycle_policy.py",
        "data_lifecycle_recovery.py",
        "data_lifecycle_repository.py",
        "data_lifecycle_retention.py",
        "data_lifecycle_scheduler.py",
        "health.py",
        "health_collector.py",
        "health_metrics.py",
        "health_policy.py",
        "health_trends.py",
    ):
        assert not (tenants / name).exists(), name


def test_worker_modules_are_nested_by_function() -> None:
    workers = ROOT / "backend/app/runtime/workers"
    expected = {"execution", "lifecycle", "scheduling"}
    assert {path.name for path in workers.iterdir() if path.is_dir()} >= expected
    assert (workers / "queue.py").is_file()
    assert not (workers / "queue").exists()
    root_modules = {
        path.stem
        for path in workers.glob("*.py")
        if path.name != "__init__.py"
    }
    assert root_modules == {"cli", "contracts", "queue"}
    assert (workers / "execution/handlers/__init__.py").is_file()
    for name in (
        "capacity.py",
        "handlers.py",
        "heartbeat.py",
        "job_handlers",
        "job_routing.py",
        "jobs.py",
        "lease_lifecycle.py",
        "lease_reporting.py",
        "maintenance.py",
        "queue_consumer.py",
        "queue_contracts.py",
        "queue_leases.py",
        "queue_queries.py",
        "queue_retries.py",
        "queue_scripts.py",
        "queue_serialization.py",
        "redis_queue.py",
        "revision_planner.py",
        "routing_payloads.py",
        "run_state.py",
        "runner.py",
        "runner_models.py",
        "scheduled_jobs.py",
        "scheduled_models.py",
        "scheduled_types.py",
        "schedules.py",
    ):
        assert not (workers / name).exists(), name


def test_self_hosted_runtime_modules_are_nested_by_function() -> None:
    runtime = ROOT / "backend/app/runtime/self_hosted"
    expected = {"enrollment", "dispatch", "worker", "projects"}
    assert {path.name for path in runtime.iterdir() if path.is_dir()} >= expected
    root_modules = {
        path.stem
        for path in runtime.glob("*.py")
        if path.name != "__init__.py"
    }
    assert root_modules == {"contracts", "models", "service"}
    for name in (
        "artifacts.py",
        "attestation.py",
        "dependencies.py",
        "dispatch.py",
        "dispatch_support.py",
        "events.py",
        "identity.py",
        "job_completion.py",
        "jobs.py",
        "maintenance.py",
        "mcp_jobs.py",
        "policy.py",
        "policy_gate.py",
        "progress.py",
        "project_files.py",
        "trust.py",
        "types.py",
        "worker_control.py",
    ):
        assert not (runtime / name).exists(), name


def test_project_modules_are_nested_by_function() -> None:
    projects = ROOT / "backend/app/domains/workspace/projects"
    expected = {"artifacts", "io", "snapshots"}
    assert {path.name for path in projects.iterdir() if path.is_dir()} >= expected
    root_modules = {
        path.stem
        for path in projects.glob("*.py")
        if path.name != "__init__.py"
    }
    assert root_modules == {
        "contracts",
        "export_models",
        "file_boundaries",
        "models",
        "policy",
        "service",
        "versioning",
    }
    for name in (
        "diffs.py",
        "output_artifacts.py",
        "run_manifest.py",
        "run_snapshots.py",
        "runtime_archive.py",
        "runtime_context.py",
        "runtime_io.py",
        "runtime_io_errors.py",
        "runtime_io_queries.py",
        "runtime_io_state.py",
        "runtime_staging.py",
        "serialization.py",
    ):
        assert not (projects / name).exists(), name


def test_agent_provider_modules_are_nested_by_function() -> None:
    providers = ROOT / "backend/app/domains/agents/providers"
    expected = {"audit", "catalog", "credentials", "health", "resolution"}
    assert {path.name for path in providers.iterdir() if path.is_dir()} >= expected
    root_modules = {
        path.stem
        for path in providers.glob("*.py")
        if path.name != "__init__.py"
    }
    assert root_modules == {"contracts"}
    for name in (
        "agent_summary.py",
        "audit_payloads.py",
        "audit_responses.py",
        "audit_writer.py",
        "capabilities.py",
        "credential_commands.py",
        "credential_queries.py",
        "health.py",
        "health_service.py",
        "health_state.py",
        "health_summary.py",
        "metadata.py",
        "model_api.py",
        "models.py",
        "policy.py",
        "resolution.py",
        "resolution_service.py",
        "resolver.py",
    ):
        assert not (providers / name).exists(), name


def test_agent_runtime_vendor_modules_are_nested_by_provider() -> None:
    runtime = ROOT / "backend/app/domains/agents/runtime"
    providers = runtime / "providers"
    assert {path.name for path in providers.iterdir() if path.is_dir()} >= {"openai", "claude"}
    assert {path.name for path in runtime.iterdir() if path.is_dir()} >= {
        "execution",
        "providers",
        "sandbox",
        "sessions",
        "tools",
    }
    for name in (
        "claude_runner.py",
        "claude_sandbox.py",
        "openai_agents.py",
        "openai_compaction.py",
        "openai_guardrails.py",
        "openai_lifecycle.py",
        "openai_results.py",
        "openai_sandbox.py",
        "openai_session.py",
        "openai_settings.py",
        "openai_streaming.py",
        "openai_tools.py",
    ):
        assert not (providers / name).exists(), name


def test_memory_modules_are_flattened_under_domain_owner() -> None:
    memory = ROOT / "backend/app/domains/agents/memory"
    expected = {
        "authorization",
        "configuration",
        "context",
        "embedding_scheduler",
        "embedding_service",
        "episodic",
        "indexing",
        "lifecycle",
        "models",
        "policy",
        "retrieval_evidence",
        "retrieval_search",
        "semantic",
        "working",
    }
    root_modules = {
        path.stem
        for path in memory.glob("*.py")
        if path.name != "__init__.py"
    }
    assert root_modules == expected
    assert {
        path.name for path in memory.iterdir() if path.is_dir() and path.name != "__pycache__"
    } == set()
    for name in (
        "access",
        "configuration",
        "context",
        "embeddings",
        "indexing",
        "lifecycle",
        "retrieval",
        "stores",
    ):
        assert not (memory / name).exists(), name


def test_operations_features_are_nested_by_function() -> None:
    operations = ROOT / "backend/app/runtime/operations"
    expected = {"metrics", "queues", "recovery", "runtimes", "timeline", "workers"}
    assert {path.name for path in operations.iterdir() if path.is_dir()} >= expected
    root_modules = {
        path.stem
        for path in operations.glob("*.py")
        if path.name != "__init__.py"
    }
    assert root_modules <= {
        "control_plane",
        "control_plane_service",
        "data_lifecycle_rollup",
        "events",
        "models",
        "operation_capacity_payloads",
        "operation_capacity",
        "outcomes",
        "overview_payloads",
        "overview_queries",
        "run_activity",
            "scheduler",
            "model_providers",
    }
    for name in (
        "dead_letters.py",
        "queue_governance.py",
        "queue_insights.py",
        "prometheus_metrics.py",
        "runtime_cleanup.py",
        "worker_capacity.py",
        "timeline.py",
        "stale_run_recovery.py",
        "utils.py",
    ):
        assert not (operations / name).exists(), name


def test_api_routes_are_nested_by_function() -> None:
    routes = ROOT / "backend/app/api/routes"
    expected = {
        "admin",
        "agents",
        "capabilities",
        "integrations",
        "operations",
        "orchestration",
        "platform",
        "self_hosted",
        "workspace",
    }
    assert {path.name for path in routes.iterdir() if path.is_dir()} >= expected
    assert {
        path.name
        for path in routes.iterdir()
        if path.is_file() and path.suffix == ".py" and path.name != "__init__.py"
    } == set()
    for name in (
        "agent_messages.py",
        "capabilities.py",
        "operations.py",
        "workspace_resources.py",
        "workspace_task_resources.py",
        "workspace_team_resources.py",
        "workspaces.py",
        "exports.py",
        "auth.py",
        "webhooks.py",
    ):
        assert not (routes / name).exists(), name


def test_api_schemas_are_nested_by_function() -> None:
    schemas = ROOT / "backend/app/api/schemas"
    expected = {
        "agents",
        "capabilities",
        "operations",
        "orchestration",
        "platform",
        "workspace",
    }
    assert {path.name for path in schemas.iterdir() if path.is_dir()} >= expected
    root_modules = {
        path.name
        for path in schemas.iterdir()
        if path.is_file() and path.suffix == ".py" and path.name != "__init__.py"
    }
    assert root_modules == {"common.py"}
    for name in (
        "agents.py",
        "agent_messages.py",
        "model_providers.py",
        "operation_capacity.py",
        "operation_events.py",
        "operations.py",
        "runs.py",
        "tasks.py",
        "team_core.py",
        "workspaces.py",
    ):
        assert not (schemas / name).exists(), name


def test_api_services_are_nested_by_function() -> None:
    services = ROOT / "backend/app/api/services"
    expected = {
        "workspace",
        "workspace/exports",
        "workspace/imports",
        "workspace/lifecycle",
    }
    for name in expected:
        assert (services / name / "__init__.py").is_file(), name
    assert {
        path.name
        for path in services.iterdir()
        if path.is_file() and path.suffix == ".py" and path.name != "__init__.py"
    } == set()
    for name in (
        "exports.py",
        "files.py",
        "workspaces.py",
        "workspace_archive_import.py",
        "workspace_import_preview.py",
        "workspace_members.py",
    ):
        assert not (services / name).exists(), name
    for name in ("tokens.py", "metadata_context.py", "metadata_support.py"):
        assert not (services / "workspace/imports" / name).exists(), name


def test_agent_profile_and_memory_modules_have_stable_owners() -> None:
    agents = ROOT / "backend/app/domains/agents"
    root_modules = {
        path.name
        for path in agents.iterdir()
        if path.is_file() and path.suffix == ".py" and path.name != "__init__.py"
    }
    assert root_modules == {"models.py", "service.py"}
    assert (agents / "profiles/__init__.py").is_file()
    memory = agents / "memory"
    assert {
        path.name
        for path in memory.iterdir()
        if path.is_file() and path.suffix == ".py" and path.name != "__init__.py"
    } == {
        "authorization.py",
        "configuration.py",
        "context.py",
        "embedding_scheduler.py",
        "embedding_service.py",
        "episodic.py",
        "indexing.py",
        "lifecycle.py",
        "models.py",
        "policy.py",
        "retrieval_evidence.py",
        "retrieval_search.py",
        "semantic.py",
        "working.py",
    }
    assert {
        path.name for path in memory.iterdir() if path.is_dir() and path.name != "__pycache__"
    } == set()
    for name in (
        "lifecycle.py",
        "payloads.py",
        "profile_commands.py",
        "queries.py",
        "reviews.py",
        "versions.py",
        "model_validation.py",
        "model_provider_summary.py",
        "memory_policy.py",
    ):
        assert not (agents / name).exists(), name
    assert not (agents / "memory/agent_policy.py").exists()


def test_runtime_space_reservations_are_owned_by_spaces_module() -> None:
    spaces = ROOT / "backend/app/runtime/environment/spaces"
    assert {
        path.name
        for path in spaces.iterdir()
        if path.is_file() and path.suffix == ".py" and path.name != "__init__.py"
    } >= {
        "reservation_attachment.py",
        "reservation_capacity.py",
        "reservation_release.py",
        "reservation_usage.py",
    }
    assert not (spaces / "reservations").exists()


def test_capability_modules_are_nested_by_function() -> None:
    capabilities = ROOT / "backend/app/domains/capabilities"
    expected = {"catalog", "governance", "mcp", "marketplace", "resources", "skills", "tools"}
    assert {path.name for path in capabilities.iterdir() if path.is_dir()} >= expected
    root_modules = {
        path.name
        for path in capabilities.iterdir()
        if path.is_file() and path.suffix == ".py" and path.name != "__init__.py"
    }
    assert root_modules == {"models.py", "service.py"}
    for name in (
        "capability_governance.py",
        "capability_governance_actions.py",
        "catalog_service.py",
        "effective_catalog.py",
        "execution.py",
        "policy_service.py",
        "product_tool_catalog.py",
        "resource_service.py",
        "resource_validation.py",
        "schema_validation.py",
        "workspace_skill_lifecycle.py",
    ):
        assert not (capabilities / name).exists(), name
    assert (capabilities / "mcp/execution/service.py").is_file()


def test_mcp_modules_are_nested_by_function() -> None:
    mcp = ROOT / "backend/app/domains/capabilities/mcp"
    expected = {"catalog", "execution", "transport"}
    assert {path.name for path in mcp.iterdir() if path.is_dir()} >= expected
    assert {
        path.name
        for path in mcp.iterdir()
        if path.is_file() and path.suffix == ".py" and path.name != "__init__.py"
    } == {"policy.py"}
    for name in (
        "adapters.py",
        "adapter_payloads.py",
        "adapter_resolver.py",
        "execution_service.py",
        "execution_invocation.py",
        "servers.py",
        "types.py",
    ):
        assert not (mcp / name).exists(), name


def test_local_application_imports_resolve_without_compatibility_shims() -> None:
    missing = []
    for path in (ROOT / "backend/app").rglob("*.py"):
        module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        package = module.rsplit(".", 1)[0]
        for node in ast.walk(ast.parse(path.read_text("utf-8-sig"))):
            targets = []
            if isinstance(node, ast.ImportFrom):
                name = "." * node.level + (node.module or "")
                targets = [importlib.util.resolve_name(name, package) if node.level else name]
            elif isinstance(node, ast.Import):
                targets = [item.name for item in node.names]
            for target in targets:
                if not target.startswith("backend.app"):
                    continue
                source = ROOT.joinpath(*target.split("."))
                if (
                    not source.with_suffix(".py").is_file()
                    and not (source / "__init__.py").is_file()
                ):
                    missing.append(f"{path.relative_to(ROOT)}:{node.lineno}: {target}")
    assert not missing, "Unresolved application imports:\n" + "\n".join(missing)


def test_all_application_modules_are_discoverable_packages() -> None:
    directories = {path.parent for path in (ROOT / "backend/app").rglob("*.py")}
    assert not [
        str(path.relative_to(ROOT))
        for path in sorted(directories)
        if not (path / "__init__.py").is_file()
    ]


def test_generic_utils_modules_are_not_used_as_dumping_grounds() -> None:
    app = ROOT / "backend/app"
    assert not list(app.rglob("utils.py"))
    assert not list(app.rglob("*utils.py"))
    assert not list(app.rglob("*helpers.py"))
    assert (app / "core/common/values.py").is_file()
    assert (app / "core/integrations/webhooks/policy.py").is_file()
    assert not (app / "core/integrations/webhooks/constants.py").exists()
    assert not (app / "domains/orchestration/requests/utils.py").exists()
    assert (app / "domains/workspace/reviews/policy.py").is_file()
    assert not (app / "domains/workspace/reviews/constants.py").exists()
    assert (app / "runtime/operations/timeline/models.py").is_file()
    assert not (app / "runtime/operations/timeline/constants.py").exists()
    assert not (app / "runtime/operations/timeline/utils.py").exists()
    assert not (app / "runtime/environment/lifecycle/guards.py").exists()
    assert not (app / "domains/orchestration/runs/state_resource_usage.py").exists()
    assert not (app / "domains/orchestration/runs/state_task_progress.py").exists()
    assert not (app / "domains/orchestration/runs/profile.py").exists()


def test_shared_normalization_and_run_queries_have_single_owners() -> None:
    app = ROOT / "backend/app"
    values = (app / "core/common/values.py").read_text(encoding="utf-8")
    assert "def stringify_or_none(" in values
    assert "def iso_datetime_or_none(" in values
    assert "def coerce_int_or_zero(" in values
    query_module = app / "domains/orchestration/runs/queries.py"
    assert query_module.is_file()
    query_source = query_module.read_text(encoding="utf-8")
    assert "def active_task_ids_by_agent(" in query_source
    assert "def task_for_run(" in query_source
    assert "def latest_events_by_run(" in query_source
    assert "def run_events_for_runs(" in query_source
    assert "def authorization_snapshot_for_run(" in query_source
    authorization = app / "domains/orchestration/requests/authorization.py"
    authorization_source = authorization.read_text(encoding="utf-8")
    assert "def authorized_task_for_run(" in authorization_source
    assert "def authorized_profile_for_run(" in authorization_source
    for path in (
        app / "domains/orchestration/workflows/scheduling/capacity.py",
        app / "domains/workspace/teams/execution/overview_repository.py",
        app / "domains/orchestration/runs/memory.py",
        app / "domains/orchestration/runs/task_progress.py",
        app / "domains/orchestration/requests/builder.py",
        app / "domains/orchestration/requests/sessions.py",
        app / "domains/orchestration/tasks/observation/repository.py",
        app / "domains/orchestration/tasks/observation/timeline.py",
    ):
        source = path.read_text(encoding="utf-8")
        assert "def active_task_ids_by_agent(" not in source
        assert "def workspace_active_task_ids_by_agent(" not in source
        assert "def _task_for_run(" not in source
        assert "def task_for_run(" not in source
        assert "def authorized_task_for_run(" not in source
        assert "def authorized_profile_for_run(" not in source
        assert "def list_run_events(" not in source
        assert "def _run_events(" not in source
        assert "def authorization_snapshot_for_run(" not in source
    assert not any(
        "def _as_utc(" in path.read_text(encoding="utf-8")
        for path in app.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    trust = app / "runtime/self_hosted/enrollment/trust.py"
    operations_trust = app / "runtime/operations/workers/self_hosted_machines.py"
    assert "def worker_trust_state(" in trust.read_text(encoding="utf-8")
    assert "def self_hosted_trust_state(" not in operations_trust.read_text(encoding="utf-8")
    for path in (
        app / "domains/workspace/teams/runtime/heartbeat.py",
        app / "domains/workspace/teams/runtime/state_builder.py",
        app / "domains/agents/runtime/tools/gateway.py",
        app / "domains/agents/runtime/tools/product.py",
    ):
        source = path.read_text(encoding="utf-8")
        assert "def _int(" not in source
        assert "def _locator_strings(" not in source
        assert "def _resource_locator_strings(" not in source


def test_application_source_directories_are_not_empty() -> None:
    app = ROOT / "backend/app"
    empty = []
    for directory in app.rglob("*"):
        if not directory.is_dir() or directory.name == "__pycache__":
            continue
        source_files = [path for path in directory.glob("*.py") if path.name != "__init__.py"]
        child_packages = [
            path
            for path in directory.iterdir()
            if path.is_dir() and path.name != "__pycache__"
        ]
        if not source_files and not child_packages:
            empty.append(str(directory.relative_to(ROOT)))
    assert not empty, "Empty application source directories: " + ", ".join(empty)


@pytest.fixture(scope="module")
def architecture_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("architecture")
    shutil.copytree(
        ROOT / "backend",
        root / "backend",
        ignore=shutil.ignore_patterns("__pycache__", "tests", "migrations"),
    )
    shutil.copy2(ROOT / "pyproject.toml", root / "pyproject.toml")
    return root


def lint(root: Path) -> subprocess.CompletedProcess[str]:
    executable = Path(sys.executable).with_name(
        "lint-imports.exe" if os.name == "nt" else "lint-imports"
    )
    return subprocess.run(
        [str(executable), "--no-cache"],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root), "NO_COLOR": "1"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_architecture_contracts_hold_without_exemptions(architecture_tree: Path) -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["tool"][
        "importlinter"
    ]
    assert not configuration.get("exclude_type_checking_imports", False)
    contracts = configuration["contracts"]
    assert len(contracts) == 7
    for contract in contracts:
        assert not contract.get("ignore_imports")
        assert not contract.get("allow_indirect_imports", False)
    result = lint(architecture_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "7 kept, 0 broken" in result.stdout


@pytest.mark.parametrize(
    ("module", "violation", "contract"),
    [
        (
            "core/db/pagination.py",
            "from backend.app.api.pagination import PageResponse",
            "Shared infrastructure cannot depend on the API even indirectly",
        ),
        (
            "core/common/pagination.py",
            "from backend.app.domains.agents.service import AgentManagementService",
            "Shared infrastructure cannot depend on the API even indirectly",
        ),
        (
            "runtime/workers/__init__.py",
            "from backend.app.api.routes import health",
            "HTTP transport is only composed by the API entry point",
        ),
        (
            "core/common/pagination.py",
            "from fastapi import Query",
            "Pagination inputs have no HTTP or database dependency",
        ),
        (
            "core/common/pagination.py",
            "import pytest",
            "Production code cannot depend on the test framework",
        ),
        (
            "api/pagination.py",
            "import docker",
            "Docker daemon access stays in its infrastructure adapter",
        ),
        (
            "api/pagination.py",
            "import boto3",
            "S3 SDK access stays in its storage adapter",
        ),
        (
            "domains/agents/runtime/execution/contracts.py",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from openai import OpenAI",
            "Agent runtime contracts do not depend on vendor SDKs or HTTP transport",
        ),
    ],
)
def test_architecture_gate_rejects_violations(
    architecture_tree: Path, module: str, violation: str, contract: str
) -> None:
    target = architecture_tree / "backend/app" / module
    original = target.read_text("utf-8")
    try:
        target.write_text(original + "\n" + violation + "\n", encoding="utf-8")
        result = lint(architecture_tree)
        assert result.returncode == 1, result.stdout + result.stderr
        assert contract + " BROKEN" in result.stdout
    finally:
        target.write_text(original, encoding="utf-8")
