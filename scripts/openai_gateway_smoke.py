"""Run the current real-model PG/Temporal/Runner/replay acceptance case explicitly.

Requires OPSMESH_TEST_DATABASE_URL and OPSMESH_TEST_MODEL_API_KEY,
OPSMESH_TEST_MODEL_BASE_URL and OPSMESH_TEST_MODEL_NAME in the environment.
Use an isolated test database: the test fixture resets its tables. The test
starts a local Temporal test server. Keys are passed only in process environment.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from opsmesh.shared.security.url_validation import validate_url_shape

ROOT = Path(__file__).resolve().parents[1]
MODEL_TEST = (
    "tests/execution/test_run_temporal.py::test_native_runner_history_codec_and_replay[real_model]"
)


@dataclass(frozen=True)
class OpenAIGatewaySmokeConfig:
    api_key: str = field(repr=False)
    base_url: str
    model: str
    database_url: str = field(repr=False)
    pytest_args: tuple[str, ...]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    dry_run = _pop_flag(args, "--dry-run")
    allowed = _pop_flag(args, "--allow-external-provider-call")
    config = config_from_env(args)
    if dry_run:
        _write_dry_run(config, stream=sys.stdout)
        return 0
    if not allowed:
        raise SystemExit("External model acceptance requires --allow-external-provider-call")
    return run_pytest(config)


def run_pytest(config: OpenAIGatewaySmokeConfig) -> int:
    env = {
        **os.environ,
        "OPSMESH_TEST_DATABASE_URL": config.database_url,
        "OPSMESH_TEST_MODEL_API_KEY": config.api_key,
        "OPSMESH_TEST_MODEL_BASE_URL": config.base_url,
        "OPSMESH_TEST_MODEL_NAME": config.model,
        "OPSMESH_TEST_TEMPORAL_LOCAL": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", MODEL_TEST, "-q", *config.pytest_args],
        env=env,
        cwd=ROOT,
        check=False,
    ).returncode


def config_from_env(argv: list[str]) -> OpenAIGatewaySmokeConfig:
    required = (
        "OPSMESH_TEST_MODEL_API_KEY",
        "OPSMESH_TEST_MODEL_BASE_URL",
        "OPSMESH_TEST_MODEL_NAME",
        "OPSMESH_TEST_DATABASE_URL",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"Required environment variables: {', '.join(missing)}")
    base_url = os.environ["OPSMESH_TEST_MODEL_BASE_URL"]
    validate_url_shape(base_url, allowed_schemes=frozenset({"https", "http"}))
    database_url = os.environ["OPSMESH_TEST_DATABASE_URL"]
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise SystemExit("OPSMESH_TEST_DATABASE_URL must refer to an isolated PostgreSQL database")
    return OpenAIGatewaySmokeConfig(
        api_key=os.environ["OPSMESH_TEST_MODEL_API_KEY"],
        base_url=base_url,
        model=os.environ["OPSMESH_TEST_MODEL_NAME"],
        database_url=database_url,
        pytest_args=tuple(argv),
    )


def _write_dry_run(config: OpenAIGatewaySmokeConfig, *, stream: TextIO) -> None:
    stream.write("OpenAI model acceptance dry run\n")
    stream.write(f"test={MODEL_TEST}\n")
    stream.write("model_key_configured=true\n")
    stream.write("model_base_url_configured=true\n")
    stream.write("model_name_configured=true\n")
    stream.write("isolated_postgresql_configured=true\n")
    stream.write("local_temporal_enabled=true\n")
    stream.write(f"pytest_arg_count={len(config.pytest_args)}\n")


def _pop_flag(argv: list[str], flag: str) -> bool:
    found = flag in argv
    while flag in argv:
        argv.remove(flag)
    return found


if __name__ == "__main__":
    raise SystemExit(main())
