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
        "runtime",
        "storage",
        "observability",
        "agent_runtime/providers",
        "agent_runtime/runtime",
        "orchestration/requests",
        "orchestration/runs",
        "orchestration/workflows",
    ):
        assert (app / name / "__init__.py").is_file(), name
    for name in (
        "runtime_manager",
        "runtime/spaces",
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
        "agent_runtime/openai",
        "agent_runtime/claude",
        "agent_runtime/sandbox",
        "orchestration/models_layer",
        "orchestration/run_request",
        "orchestration/planning",
        "orchestration/policies",
        "orchestration/state",
        "orchestration/steps",
        "orchestration/scheduler",
        "orchestration/runtime",
    ):
        assert not list((app / name).rglob("*.py")), name


@pytest.mark.parametrize(
    "module",
    [
        "backend.app.operations.worker_lifecycle",
        "backend.app.operations.stale_run_recovery",
        "backend.app.teams.execution_loop",
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
    teams = ROOT / "backend/app/teams"
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
        "agent_messages/pagination.py",
    ):
        assert not (teams / name).exists(), name


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
            "db/pagination.py",
            "from backend.app.api.pagination import PageResponse",
            "Shared infrastructure cannot depend on the API even indirectly",
        ),
        (
            "core/pagination.py",
            "from backend.app.agents.service import AgentManagementService",
            "Shared infrastructure cannot depend on the API even indirectly",
        ),
        (
            "workers/__init__.py",
            "from backend.app.api.routes import health",
            "HTTP transport is only composed by the API entry point",
        ),
        (
            "core/pagination.py",
            "from fastapi import Query",
            "Pagination inputs have no HTTP or database dependency",
        ),
        (
            "core/pagination.py",
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
            "agent_runtime/contracts.py",
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
