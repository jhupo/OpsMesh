import subprocess

import pytest

from scripts import ci_command


@pytest.mark.parametrize("returncode", [0, 1])
def test_ci_command_preserves_exit_status_and_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], returncode: int
) -> None:
    monkeypatch.setattr("sys.argv", ["ci_command.py", "checker", "path with spaces"])

    def run(
        args: list[str], *, capture_output: bool, text: bool, check: bool
    ) -> subprocess.CompletedProcess[str]:
        assert args == ["checker", "path with spaces"]
        assert capture_output and text and not check
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
            ["checker"], 2, "token=private-value", "password=private-password"
        ),
    )
    assert ci_command.main() == 2
    output = capsys.readouterr()
    assert "private-" not in output.out + output.err
    assert "::error title=CI validation failed::[redacted]" in output.out
