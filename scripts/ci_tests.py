"""Focused PR validation; the full suite belongs only to release-prepare.yml."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def select_tests(changed: list[str]) -> list[str]:
    # Select product boundaries, not helper filenames or implementation classes.
    flows = {
        "access": {"test_auth_api.py", "test_tenant_isolation_matrix.py"},
        "agents": {"test_agent_runtime_critical_e2e.py"},
        "capabilities": {"test_capabilities_api.py", "test_marketplace_api.py"},
        "integrations": {"test_user_orchestration.py", "test_webhooks.py"},
        "orchestration": {"test_user_orchestration.py", "test_agent_runtime_recovery_e2e.py"},
        "workspace": {"test_workspace_projects.py", "test_workspace_export_api.py"},
        "platform": {"test_platform_updates.py"},
    }
    tests: set[str] = set()
    for name in changed:
        path = Path(name)
        if name.startswith("operator/"):
            tests.update({"test_operator_security.py", "test_platform_updates.py"})
        if name.startswith(("deploy/", ".github/workflows/")):
            tests.add("test_deployment_assets.py")
        if name.startswith("scripts/release"):
            tests.add("test_release_delivery.py")
        if name in {"pyproject.toml", "uv.lock"}:
            tests.update({"test_health.py", "test_user_orchestration.py"})
        if name.startswith("backend/tests/test_") and path.is_file():
            tests.add(path.name)
        if name.startswith("backend/app/"):
            tests.add("test_health.py")
            for domain, targets in flows.items():
                if name.startswith(
                    (f"backend/app/domains/{domain}/", f"backend/app/api/routes/{domain}/")
                ):
                    tests.update(targets)
            if name.startswith("backend/app/runtime/"):
                tests.add("test_agent_runtime_recovery_e2e.py")
            if name.startswith("backend/app/observability/"):
                evidence_flows = {
                    "audit": "test_audit_integrity_api.py",
                    "costs": "test_cost_api.py",
                    "notifications": "test_notifications_api.py",
                    "telemetry": "test_operations_api.py",
                }
                target = evidence_flows.get(path.parts[3])
                if target is not None:
                    tests.add(target)
    return sorted(f"backend/tests/{name}" for name in tests)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    if not args.base or set(args.base) == {"0"}:
        parser.error("A real base commit is required")
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", f"{args.base}...HEAD"], text=True
    ).splitlines()
    tests = select_tests(changed)
    if not tests:
        print("No affected product flows; static checks cover this change.", flush=True)
        return 0
    print("Focused test targets:", *tests, flush=True)
    return subprocess.run([sys.executable, "-m", "pytest", *tests], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
