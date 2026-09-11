"""Command-line entrypoint for the OpsMesh self-hosted MCP connector."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from filelock import FileLock, Timeout

from .connector_api import HttpMcpJobApi
from .connector_models import ConnectorApiError, ConnectorStateError
from .connector_state import ConnectorStateStore
from .connector_worker import SelfHostedMcpConnector
from .mcp_stdio_client import capability_report

DEFAULT_STATE_PATH = Path.home() / ".opsmesh" / "connector-state.sqlite3"


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.check:
        print(json.dumps(capability_report(), ensure_ascii=False, separators=(",", ":")))
        return 0
    api_url = arguments.api_url or os.getenv("OPSMESH_API_URL", "")
    credential = os.getenv("OPSMESH_RUNTIME_CREDENTIAL", "")
    if not api_url or not credential:
        print(
            "OPSMESH_API_URL and OPSMESH_RUNTIME_CREDENTIAL are required",
            file=sys.stderr,
        )
        return 2
    try:
        state_path = Path(
            arguments.state_path
            or os.getenv("OPSMESH_CONNECTOR_STATE_PATH", "")
            or DEFAULT_STATE_PATH
        ).expanduser()
        capabilities = _capabilities(arguments.capabilities_file)
        attestation = _attestation(arguments.attestation_file)
        state_path.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        print(f"Self-hosted connector configuration failed: {exc}", file=sys.stderr)
        return 2
    lock = FileLock(f"{state_path}.lock")
    try:
        lock.acquire(timeout=0)
    except Timeout:
        print("Another connector process owns this recovery state", file=sys.stderr)
        return 2
    try:
        with (
            ConnectorStateStore(state_path) as state,
            HttpMcpJobApi(
                api_url=api_url,
                credential=credential,
                timeout_seconds=arguments.request_timeout_seconds,
            ) as api,
        ):
            api.heartbeat(capabilities, attestation)
            connector = SelfHostedMcpConnector(api=api, state=state)
            if arguments.once:
                outcome = connector.run_once()
                print(json.dumps({"status": outcome}, separators=(",", ":")))
                return 0
            return _run_forever(
                api=api,
                connector=connector,
                capabilities=capabilities,
                attestation=attestation,
                poll_interval_seconds=arguments.poll_interval_seconds,
                heartbeat_interval_seconds=arguments.heartbeat_interval_seconds,
            )
    except (ConnectorApiError, ConnectorStateError, OSError, ValueError) as exc:
        print(f"Self-hosted connector stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        lock.release()


def _run_forever(
    *,
    api: HttpMcpJobApi,
    connector: SelfHostedMcpConnector,
    capabilities: dict[str, object],
    attestation: dict[str, object] | None,
    poll_interval_seconds: float,
    heartbeat_interval_seconds: float,
) -> int:
    next_heartbeat = time.monotonic() + heartbeat_interval_seconds
    try:
        while True:
            try:
                if time.monotonic() >= next_heartbeat:
                    api.heartbeat(capabilities, attestation)
                    next_heartbeat = time.monotonic() + heartbeat_interval_seconds
                outcome = connector.run_once()
            except ConnectorApiError as exc:
                if not exc.retryable:
                    raise
                print(
                    "Self-hosted connector control-plane request failed; retrying",
                    file=sys.stderr,
                )
                outcome = "idle"
            if outcome == "idle":
                time.sleep(poll_interval_seconds)
    except KeyboardInterrupt:
        return 0


def _capabilities(path_value: str | None) -> dict[str, object]:
    return _json_object_file(path_value, "Capabilities")


def _attestation(path_value: str | None) -> dict[str, object] | None:
    if not path_value:
        return None
    return _json_object_file(path_value, "Attestation")


def _json_object_file(path_value: str | None, label: str) -> dict[str, object]:
    if not path_value:
        return {}
    try:
        payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} file must contain a JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} file must contain a JSON object")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the OpsMesh self-hosted MCP connector")
    parser.add_argument("--api-url", help="OpsMesh API prefix, for example https://host/api/v1")
    parser.add_argument("--state-path")
    parser.add_argument("--capabilities-file")
    parser.add_argument("--attestation-file")
    parser.add_argument("--poll-interval-seconds", type=_positive_float, default=2.0)
    parser.add_argument("--heartbeat-interval-seconds", type=_positive_float, default=30.0)
    parser.add_argument("--request-timeout-seconds", type=_positive_float, default=30.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true")
    return parser


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
