from __future__ import annotations

import importlib.util
import sys
from io import StringIO
from pathlib import Path
from types import ModuleType

import pytest


def test_openai_gateway_smoke_config_requires_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(SystemExit, match="OPENAI_API_KEY is required"):
        script.config_from_env([])


def test_openai_gateway_smoke_config_normalizes_root_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_SMOKE_BASE_URL", "https://dash.ovload.com/")

    config = script.config_from_env(["-k", "uses_tool"])

    assert config.base_url == "https://dash.ovload.com/v1"
    assert config.model == "gpt-4.1-nano"
    assert config.model_api is None
    assert config.pytest_args == ("-k", "uses_tool")


def test_openai_gateway_smoke_config_reads_model_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_SMOKE_MODEL_API", "chat_completions")

    config = script.config_from_env([])

    assert config.model_api == "chat_completions"


def test_openai_gateway_smoke_script_does_not_embed_api_key() -> None:
    source = _script_path().read_text(encoding="utf-8")

    assert "sk-" not in source
    assert "OPENAI_API_KEY" in source


def test_openai_gateway_smoke_dry_run_redacts_key_and_skips_pytest(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _load_script()
    calls: list[object] = []
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.setenv("OPENAI_SMOKE_BASE_URL", "https://dash.ovload.com/")
    monkeypatch.setattr(script, "run_pytest", lambda config: calls.append(config) or 1)

    exit_code = script.main(["--dry-run", "-k", "uses_tool"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert calls == []
    assert "api_key_configured=true" in output
    assert "api_key_prefix=sk-" in output
    assert "api_key_length=14" in output
    assert "sk-test-secret" not in output
    assert "base_url=https://dash.ovload.com/v1" in output
    assert "model_api=unspecified" in output
    assert "pytest_args=-k uses_tool" in output


def test_openai_gateway_smoke_requires_explicit_external_call_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    calls: list[object] = []
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.setattr(script, "run_pytest", lambda config: calls.append(config) or 1)

    with pytest.raises(SystemExit, match="--allow-external-provider-call"):
        script.main(["-k", "uses_tool"])

    assert calls == []


def test_openai_gateway_smoke_external_call_flag_runs_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    calls: list[object] = []
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.setenv("OPENAI_SMOKE_BASE_URL", "https://dash.ovload.com/")
    monkeypatch.setenv("OPENAI_SMOKE_MODEL_API", "chat_completions")
    monkeypatch.setattr(script, "run_pytest", lambda config: calls.append(config) or 0)

    exit_code = script.main(["--allow-external-provider-call", "-k", "uses_tool"])

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0].base_url == "https://dash.ovload.com/v1"
    assert calls[0].model_api == "chat_completions"
    assert calls[0].pytest_args == ("-k", "uses_tool")


def test_openai_gateway_smoke_dry_run_writer_redacts_key() -> None:
    script = _load_script()
    stream = StringIO()
    config = script.OpenAIGatewaySmokeConfig(
        api_key="sk-test-secret",
        base_url="https://dash.ovload.com/v1",
        model="gpt-4.1-nano",
        model_api="chat_completions",
        pytest_args=("-k", "uses_tool"),
    )

    script._write_dry_run(config, stream=stream)

    output = stream.getvalue()
    assert "sk-test-secret" not in output
    assert "api_key_length=14" in output
    assert "model_api=chat_completions" in output


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "openai_gateway_smoke",
        _script_path(),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _script_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "openai-gateway-smoke.py"
