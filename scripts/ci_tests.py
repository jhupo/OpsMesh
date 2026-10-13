"""Focused PR validation; the full suite belongs only to release-prepare.yml."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

DOMAIN_TESTS = {
    "identity": {"test_auth_api.py", "test_tenant_isolation_matrix.py", "test_admin_api.py"},
    "agents": {
        "test_agent_configuration_api.py",
        "test_agent_execution_critical_e2e.py",
        "test_temporal_agent_execution.py",
        "test_temporal_conversation_sessions_postgres.py",
        "test_temporal_model_policy_postgres.py",
    },
    "capabilities": {"test_capabilities_api.py", "test_marketplace_api.py"},
    "orchestration": {
        "test_user_orchestration.py",
        "test_temporal_node_execution_postgres.py",
        "test_temporal_automation_execution.py",
        "test_task_control_postgres.py",
        "test_webhooks.py",
    },
    "workspaces": {"test_workspace_projects.py", "test_workspace_export_api.py"},
    "teams": {
        "test_team_operations_api.py",
        "test_team_sessions_api.py",
        "test_temporal_team_orchestration_postgres.py",
        "test_team_reconciliation_api_postgres.py",
    },
    "resources": {"test_file_api.py", "test_workspace_export_api.py"},
    "messaging": {"test_notifications_api.py", "test_platform_mail_invitations.py"},
    "governance": {"test_audit_integrity_api.py", "test_cost_api.py", "test_operations_api.py"},
    "runtime": {
        "test_temporal_outbox_postgres.py",
        "test_temporal_sessions_postgres.py",
        "test_temporal_worker_bootstrap.py",
        "test_temporal_worker_ownership.py",
        "test_async_worker_control.py",
        "test_native_sandbox_docker_postgres.py",
    },
    "shared": {"test_auth_api.py", "test_user_orchestration.py", "test_operations_api.py"},
    "bootstrap": {
        "test_model_registration.py",
        "test_temporal_worker_bootstrap.py",
        "test_admin_api.py",
    },
    "platform": {
        "test_platform_updates.py",
        "test_execution_admission_postgres.py",
        "test_platform_maintenance_temporal_postgres.py",
    },
}

BOUNDARY_TESTS = {
    "src/opsmesh/orchestration/runs/": {
        "test_conversation_cancellation_boundary_postgres.py",
        "test_task_control_api.py",
        "test_task_control_postgres.py",
        "test_temporal_native_sandbox_postgres.py",
        "test_native_sandbox_docker_postgres.py",
    },
    "src/opsmesh/agents/execution/providers/openai/": {
        "test_agent_native_sandbox_postgres.py",
        "test_temporal_native_sandbox_postgres.py",
        "test_native_sandbox_docker_postgres.py",
    },
    "src/opsmesh/runtime/sandboxes/": {
        "test_agent_native_sandbox_postgres.py",
        "test_temporal_native_sandbox_postgres.py",
        "test_native_sandbox_docker_postgres.py",
    },
    "src/opsmesh/capabilities/mcp/": {
        "test_native_mcp_protocol.py",
        "test_native_sandbox_docker_postgres.py",
        "test_temporal_node_execution_postgres.py",
    },
    "src/opsmesh/capabilities/plugins/": {
        "test_plugin_download_temporal_postgres.py",
        "test_user_orchestration.py",
    },
    "src/opsmesh/resources/memory/": {
        "test_knowledge_memory_tools_postgres.py",
        "test_temporal_memory_model_postgres.py",
        "test_memory_lifecycle.py",
    },
    "src/opsmesh/resources/knowledge/": {"test_knowledge_memory_tools_postgres.py"},
    "src/opsmesh/resources/transfers/": {
        "test_workspace_export_api.py",
        "test_platform_maintenance_temporal_postgres.py",
        "test_archive_temporal.py",
        "test_archive_consistency_postgres.py",
    },
    "src/opsmesh/orchestration/conversations/": {
        "test_conversation_cancellation_boundary_postgres.py",
        "test_conversations_postgres.py",
        "test_conversation_stream_postgres.py",
        "test_temporal_conversation_sessions_postgres.py",
    },
    "src/opsmesh/orchestration/tasks/": {
        "test_task_observation_api.py",
        "test_task_control_api.py",
        "test_task_planning_api.py",
    },
    "tests/fixtures/": {
        "test_auth_api.py",
        "test_temporal_outbox_postgres.py",
        "test_temporal_conversation_sessions_postgres.py",
        "test_temporal_node_execution_postgres.py",
        "test_temporal_team_orchestration_postgres.py",
    },
    "migrations/": {
        "test_execution_release_migration_postgres.py",
        "test_execution_timestamp_migration.py",
        "test_model_registration.py",
        "test_sdk_sessions_postgres.py",
        "test_openai_execution_migration_postgres.py",
    },
    "deploy/": {"test_deployment_assets.py"},
    "deploy/images/": {"test_native_sandbox_docker_postgres.py"},
    ".github/workflows/": {"test_deployment_assets.py", "test_ci_command.py"},
    "operator/": {
        "test_operator_security.py",
        "test_operator_commands.py",
        "test_platform_updates.py",
    },
    "scripts/release": {"test_release_delivery.py"},
    "scripts/check_candidate.py": {"test_deployment_assets.py", "test_operator_security.py"},
    "scripts/remote-backend-validation.sh": {"test_deployment_assets.py"},
    "scripts/check_runtime_image.py": {
        "test_deployment_assets.py",
        "test_native_sandbox_docker_postgres.py",
    },
    "scripts/build_standalone.py": {"test_release_delivery.py", "test_deployment_assets.py"},
    "scripts/export_compute_requirements.py": {"test_deployment_assets.py"},
    "scripts/server-smoke-test.sh": {"test_deployment_assets.py"},
    "src/opsmesh/runtime/operations/metrics/": {"test_metrics.py"},
}

NATIVE_SANDBOX_TEST = "tests/test_native_sandbox_docker_postgres.py"


def select_tests(changed: list[str]) -> list[str]:
    # Select product boundaries, not helper filenames or implementation classes.
    tests: set[str] = set()
    for name in changed:
        path = Path(name)
        for prefix, targets in BOUNDARY_TESTS.items():
            if name.startswith(prefix):
                tests.update(targets)
        if name in {"scripts/ci_tests.py", "scripts/ci_command.py"}:
            tests.add("test_ci_command.py")
        if name in {"pyproject.toml", "uv.lock"}:
            tests.update(
                {
                    "test_health.py",
                    "test_user_orchestration.py",
                    "test_temporal_worker_bootstrap.py",
                    "test_native_sandbox_docker_postgres.py",
                }
            )
        if name.startswith("tests/test_") and path.is_file():
            tests.add(path.name)
        if name.startswith("src/opsmesh/"):
            tests.add("test_health.py")
            for domain, targets in DOMAIN_TESTS.items():
                if name.startswith(f"src/opsmesh/{domain}/"):
                    tests.update(targets)
    selected = sorted(f"tests/{name}" for name in tests)
    missing = [name for name in selected if not Path(name).is_file()]
    if missing:
        raise ValueError(f"Focused product tests do not exist: {', '.join(missing)}")
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    if not args.base or set(args.base) == {"0"}:
        parser.error("A real base commit is required")
    changed = subprocess.check_output(
        ["git", "diff", "--name-status", "--find-renames", f"{args.base}...HEAD"], text=True
    ).splitlines()
    paths = []
    for entry in changed:
        fields = entry.split("\t")
        status, path = fields[0], fields[-1]
        # A relocated test tree is validated by affected domain flows; running every
        # renamed file would turn a source-layout change into the release-only suite.
        if status.startswith("R") and path.startswith("tests/"):
            continue
        paths.append(path)
    tests = select_tests(paths)
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"has_tests={str(bool(tests)).lower()}\n")
            output.write(f"native_sandbox={str(NATIVE_SANDBOX_TEST in tests).lower()}\n")
        print("Focused test targets:", *tests, flush=True)
        return 0
    if not tests:
        print("No affected product flows; static checks cover this change.", flush=True)
        return 0
    print("Focused test targets:", *tests, flush=True)
    return subprocess.run([sys.executable, "-m", "pytest", *tests], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
