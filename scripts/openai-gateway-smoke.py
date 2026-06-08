#!/usr/bin/env python
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import TextIO

from backend.app.model_providers.base_url import normalize_openai_compatible_base_url


@dataclass(frozen=True)
class OpenAIGatewaySmokeConfig:
    api_key: str
    base_url: str | None
    model: str
    model_api: str | None
    pytest_args: tuple[str, ...]


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    dry_run = _pop_flag(args, "--dry-run")
    allow_external_provider_call = _pop_flag(args, "--allow-external-provider-call")
    config = config_from_env(args)
    if dry_run:
        _write_dry_run(config, stream=sys.stdout)
        return 0
    if not allow_external_provider_call:
        raise SystemExit(
            "Refusing to call external provider without --allow-external-provider-call"
        )
    return run_pytest(config)


def run_pytest(config: OpenAIGatewaySmokeConfig) -> int:
    env = dict(os.environ)
    env["OPENAI_API_KEY"] = config.api_key
    if config.base_url is not None:
        env["OPENAI_SMOKE_BASE_URL"] = config.base_url
    env["OPENAI_SMOKE_MODEL"] = config.model
    if config.model_api is not None:
        env["OPENAI_SMOKE_MODEL_API"] = config.model_api
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "backend/tests/test_agent_runtime.py",
            "-q",
            "-m",
            "openai_smoke",
            *config.pytest_args,
        ],
        env=env,
        check=False,
    ).returncode


def config_from_env(argv: list[str]) -> OpenAIGatewaySmokeConfig:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required")
    base_url = normalize_openai_compatible_base_url(
        os.environ.get("OPENAI_SMOKE_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    )
    model = os.environ.get("OPENAI_SMOKE_MODEL", "gpt-4.1-nano")
    model_api = os.environ.get("OPENAI_SMOKE_MODEL_API")
    return OpenAIGatewaySmokeConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        model_api=model_api,
        pytest_args=tuple(argv),
    )


def _write_dry_run(config: OpenAIGatewaySmokeConfig, *, stream: TextIO) -> None:
    stream.write("OpenAI gateway smoke dry run\n")
    stream.write("api_key_configured=true\n")
    stream.write(f"api_key_prefix={config.api_key[:3]}\n")
    stream.write(f"api_key_length={len(config.api_key)}\n")
    stream.write(f"base_url={config.base_url or 'openai-default'}\n")
    stream.write(f"model={config.model}\n")
    stream.write(f"model_api={config.model_api or 'sdk-default'}\n")
    stream.write(f"pytest_args={' '.join(config.pytest_args)}\n")


def _pop_flag(argv: list[str], flag: str) -> bool:
    found = flag in argv
    while flag in argv:
        argv.remove(flag)
    return found


if __name__ == "__main__":
    raise SystemExit(main())
