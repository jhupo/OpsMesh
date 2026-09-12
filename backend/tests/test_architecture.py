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
    for name in (
        "workspace/domains",
        "workspace/projects",
        "workspace/reviews",
        "workspace/storage",
        "workspace/teams",
        "workspace/teams/execution",
        "workspace/teams/operations",
        "workspace/teams/projects",
        "workspace/teams/providers",
        "workspace/teams/organization",
        "workspace/teams/runtime",
        "workspace/tenants",
        "platform/admin",
        "platform/auth",
        "platform/common",
        "platform/db",
        "platform/identity",
        "platform/integrations",
        "platform/integrations/webhooks",
        "platform/rate_limits",
        "platform/redis",
        "platform/secrets",
        "platform/security",
        "execution/runtime",
        "execution/runtime/backends",
        "execution/runtime/commands",
        "execution/runtime/lifecycle",
        "execution/runtime/pool",
        "execution/runtime/policies",
        "execution/runtime/spaces",
        "execution/runtime/spaces/reservations",
        "execution/workers",
        "execution/operations",
        "execution/operations/metrics",
        "execution/operations/queues",
        "execution/operations/recovery",
        "execution/operations/runtimes",
        "execution/operations/timeline",
        "execution/operations/workers",
        "execution/self_hosted",
        "observability",
        "agents/runtime/providers",
        "agents/runtime/runtime",
        "orchestration/approvals",
        "orchestration/requests",
        "orchestration/runs",
        "orchestration/tasks",
        "orchestration/workflows",
    ):
        assert (app / name / "__init__.py").is_file(), name
    for name in (
        "platform/core",
        "admin",
        "auth",
        "core",
        "db",
        "domains",
        "identity",
        "projects",
        "rate_limits",
        "redis",
        "reviews",
        "secrets",
        "security",
        "storage",
        "teams",
        "webhooks",
        "workspaces",
        "runtime_manager",
        "runtime",
        "workers",
        "operations",
        "self_hosted",
        "runtime/spaces",
        "runtime/spaces/helpers.py",
        "planning",
        "teams/project_space",
        "workers/queue",
        "tools/product_tools",
        "scheduled_jobs",
        "runtime_spaces",
        "runtimes",
        "files",
        "artifacts",
        "exports",
        "audit",
        "costs",
        "telemetry",
        "notifications",
        "agent_runtime",
        "agent_messages",
        "model_providers",
        "memory",
        "tools",
        "marketplace",
        "approvals",
        "tasks",
        "runs",
        "orchestration/models_layer",
        "orchestration/run_request",
        "orchestration/planning",
        "orchestration/policies",
        "orchestration/state",
        "orchestration/steps",
        "orchestration/scheduler",
        "orchestration/runtime",
        "platform/common/typing.py",
        "execution/operations/utils.py",
    ):
        target = app / name
        if target.suffix == ".py":
            assert not target.exists(), name
        else:
            assert not list(target.rglob("*.py")), name


@pytest.mark.parametrize(
    "module",
    [
        "backend.app.execution.operations.workers.lifecycle",
        "backend.app.execution.operations.recovery.service",
        "backend.app.workspace.teams.execution_loop",
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
    teams = ROOT / "backend/app/workspace/teams"
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
        "agents/messages/pagination.py",
        "orchestration/tasks/control_payloads.py",
        "execution/workers/job_handlers/base.py",
        "workspace/teams/operating_context.py",
        "api/services/workspace_export_constants.py",
        "api/schemas/redaction.py",
    ):
        assert not (app / name).exists(), name


def test_team_features_are_nested_by_function() -> None:
    teams = ROOT / "backend/app/workspace/teams"
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


def test_runtime_features_are_nested_by_function() -> None:
    runtime = ROOT / "backend/app/execution/runtime"
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


def test_operations_features_are_nested_by_function() -> None:
    operations = ROOT / "backend/app/execution/operations"
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
    agents = ROOT / "backend/app/agents"
    root_modules = {
        path.name
        for path in agents.iterdir()
        if path.is_file() and path.suffix == ".py" and path.name != "__init__.py"
    }
    assert root_modules == {"models.py", "service.py"}
    assert (agents / "profiles/__init__.py").is_file()
    assert (agents / "memory/policy.py").is_file()
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


def test_capability_modules_are_nested_by_function() -> None:
    capabilities = ROOT / "backend/app/capabilities"
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
    mcp = ROOT / "backend/app/capabilities/mcp"
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
            "platform/db/pagination.py",
            "from backend.app.api.pagination import PageResponse",
            "Shared infrastructure cannot depend on the API even indirectly",
        ),
        (
            "platform/common/pagination.py",
            "from backend.app.agents.service import AgentManagementService",
            "Shared infrastructure cannot depend on the API even indirectly",
        ),
        (
            "execution/workers/__init__.py",
            "from backend.app.api.routes import health",
            "HTTP transport is only composed by the API entry point",
        ),
        (
            "platform/common/pagination.py",
            "from fastapi import Query",
            "Pagination inputs have no HTTP or database dependency",
        ),
        (
            "platform/common/pagination.py",
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
            "agents/runtime/contracts.py",
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
