#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


def main() -> int:
    args = _parse_args()
    webhook_url = _validated_webhook_url(
        args.webhook_url or os.environ.get("OPSMESH_ALERT_WEBHOOK_URL", "")
    )
    bearer_token = os.environ.get("OPSMESH_ALERT_WEBHOOK_BEARER_TOKEN")
    output = Path(
        args.output
        or os.environ.get(
            "OPSMESH_ALERTMANAGER_CONFIG_FILE",
            "/etc/opsmesh/alertmanager.generated.yml",
        )
    ).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    content = _render(webhook_url, bearer_token)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=output.parent,
        prefix=f".{output.name}.",
        delete=False,
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.chmod(temporary, 0o600)
    temporary.replace(output)
    print(f"Wrote Alertmanager configuration to {output}")
    return 0


def _validated_webhook_url(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise SystemExit("OPSMESH_ALERT_WEBHOOK_URL is required")
    parsed = urlsplit(normalized)
    if parsed.username is not None or parsed.password is not None:
        raise SystemExit("Alert webhook URL must not contain credentials")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SystemExit("Alert webhook URL must be an absolute HTTP(S) URL")
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("Alert webhook URL must use HTTPS unless it targets loopback")
    return normalized


def _render(webhook_url: str, bearer_token: str | None) -> str:
    authorization = ""
    if bearer_token:
        authorization = (
            "        http_config:\n"
            "          authorization:\n"
            "            type: Bearer\n"
            f"            credentials: {json.dumps(bearer_token)}\n"
        )
    return (
        "global:\n"
        "  resolve_timeout: 5m\n\n"
        "route:\n"
        "  receiver: opsmesh-operators\n"
        "  group_by: [alertname, service, severity]\n"
        "  group_wait: 30s\n"
        "  group_interval: 5m\n"
        "  repeat_interval: 4h\n\n"
        "receivers:\n"
        "  - name: opsmesh-operators\n"
        "    webhook_configs:\n"
        f"      - url: {json.dumps(webhook_url)}\n"
        "        send_resolved: true\n"
        f"{authorization}"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a secret-safe Alertmanager config")
    parser.add_argument("--output")
    parser.add_argument("--webhook-url")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
