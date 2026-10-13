import re
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import ci_command, ci_tests


@pytest.mark.parametrize("returncode", [0, 1])
def test_ci_command_preserves_exit_status_and_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], returncode: int
) -> None:
    monkeypatch.setattr("sys.argv", ["ci_command.py", "checker", "path with spaces"])

    def run(
        args: list[str], *, stdout: int, stderr: int, text: bool, check: bool
    ) -> subprocess.CompletedProcess[str]:
        assert args == ["checker", "path with spaces"]
        assert stdout == subprocess.PIPE and stderr == subprocess.STDOUT and text and not check
        return subprocess.CompletedProcess(args, returncode, "diagnostic 100%\nnext line", "")

    monkeypatch.setattr(ci_command.subprocess, "run", run)
    assert ci_command.main() == returncode
    output = capsys.readouterr().out
    if returncode:
        assert "::error title=CI validation failed::diagnostic 100%25%0Anext line" in output
    else:
        assert "::error" not in output


def test_ci_command_redacts_secrets_before_public_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["ci_command.py", "checker"])
    monkeypatch.setattr(
        ci_command.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            ["checker"],
            2,
            "token=private-value\npassword=private-password\nFAILED test_release - assertion",
        ),
    )
    assert ci_command.main() == 2
    output = capsys.readouterr()
    assert "private-" not in output.out + output.err
    assert "::error title=CI validation failed::[redacted]" in output.out
    assert "FAILED test_release - assertion" in output.out


def test_ci_tests_propagates_pytest_failure_without_wrapper_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["ci_tests.py", "--base", "a1b2c3d"])
    monkeypatch.setattr(
        ci_tests.subprocess,
        "check_output",
        lambda *args, **kwargs: "M\tsrc/opsmesh/messaging/notifications/service.py",
    )

    def run(args: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check is False
        assert args[1:3] == ["-m", "pytest"]
        assert "tests/test_notifications_api.py" in args
        assert "tests/test_health.py" in args
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(ci_tests.subprocess, "run", run)
    assert ci_tests.main() == 1


def test_test_tree_relocation_keeps_ci_focused_on_product_boundaries(monkeypatch):
    monkeypatch.setattr("sys.argv", ["ci_tests.py", "--base", "a1b2c3d"])
    monkeypatch.setattr(
        ci_tests.subprocess,
        "check_output",
        lambda *args, **kwargs: (
            "R095\told-tests/test_agent_execution.py\ttests/test_agent_runtime.py\n"
            "R090\told-app/agents/service.py\tsrc/opsmesh/agents/service.py\n"
            "M\ttests/test_ci_command.py\n"
        ),
    )

    def run(args, *, check):
        assert "tests/test_agent_execution.py" not in args
        assert "tests/test_agent_execution_critical_e2e.py" in args
        assert "tests/test_ci_command.py" in args
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(ci_tests.subprocess, "run", run)
    assert ci_tests.main() == 0


@pytest.mark.parametrize(
    "source,required",
    [
        ("src/opsmesh/agents/execution/workflow.py", "test_temporal_agent_execution.py"),
        (
            "src/opsmesh/capabilities/plugins/downloads/service.py",
            "test_plugin_download_temporal_postgres.py",
        ),
        ("src/opsmesh/resources/transfers/activities.py", "test_archive_temporal.py"),
        ("src/opsmesh/runtime/temporal/worker.py", "test_native_sandbox_docker_postgres.py"),
        ("src/opsmesh/runtime/operations/metrics/temporal.py", "test_metrics.py"),
    ],
)
def test_focused_selection_keeps_native_product_boundaries(source: str, required: str) -> None:
    selected = ci_tests.select_tests([source])
    assert f"tests/{required}" in selected
    assert all(Path(name).is_file() for name in selected)
    assert len(selected) < len(list(Path("tests").glob("test_*.py")))
    assert "tests/test_worker_runner.py" not in selected
    assert "tests/test_workspace_api.py" not in selected


def test_focused_selection_rejects_missing_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(ci_tests.DOMAIN_TESTS, "agents", {"test_missing_product_flow.py"})
    with pytest.raises(ValueError, match="test_missing_product_flow"):
        ci_tests.select_tests(["src/opsmesh/agents/service.py"])


def test_github_selection_reports_native_docker_requirement_without_running_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "github-output"
    monkeypatch.setattr(
        "sys.argv", ["ci_tests.py", "--base", "a1b2c3d", "--github-output", str(output)]
    )
    monkeypatch.setattr(
        ci_tests.subprocess,
        "check_output",
        lambda *args, **kwargs: "M\tsrc/opsmesh/runtime/sandboxes/service.py",
    )
    assert ci_tests.main() == 0
    assert output.read_text() == "has_tests=true\nnative_sandbox=true\n"
    assert ci_tests.select_tests(["docs/README.md"]) == []


def test_product_ci_enables_real_infrastructure_and_release_runs_full_suite() -> None:
    backend = yaml.safe_load(Path(".github/workflows/backend-ci.yml").read_text())
    release = yaml.safe_load(Path(".github/workflows/release-prepare.yml").read_text())
    focused = backend["jobs"]["backend"]
    assert focused["services"]["postgres"]["image"].startswith("pgvector/pgvector:")
    assert {"postgres", "redis"} <= focused["services"].keys()
    for job in (focused, release["jobs"]["gate"]):
        assert job["env"]["OPSMESH_TEST_POSTGRES_URL"].startswith("postgresql+psycopg://")
        assert job["env"]["OPSMESH_TEST_TEMPORAL"] == "1"
        assert job["env"]["OPSMESH_TEST_NATIVE_SANDBOX"] == "1"
        assert job["env"]["OPSMESH_TEST_COMPUTE_IMAGE"]
        assert any("Dockerfile.runtime" in step.get("run", "") for step in job["steps"])
    assert any("ci_tests.py --base" in step.get("run", "") for step in focused["steps"])
    assert any(
        step.get("run", "").endswith("uv run pytest") for step in release["jobs"]["gate"]["steps"]
    )
    for path in Path(".github/workflows").glob("*.yml"):
        content = path.read_text()
        assert "tests/test_worker_runner.py" not in content
        assert "tests/test_workspace_api.py" not in content
        assert "tests/test_runtime_allocations_postgres.py" not in content
        for target in re.findall(r"tests/test_[a-z0-9_]+\.py", content):
            assert Path(target).is_file(), f"{path}: deleted product target {target}"


def test_all_focused_domain_and_cross_module_targets_exist() -> None:
    targets = {
        name
        for mapping in (ci_tests.DOMAIN_TESTS, ci_tests.BOUNDARY_TESTS)
        for group in mapping.values()
        for name in group
    }
    assert all((Path("tests") / name).is_file() for name in targets)
