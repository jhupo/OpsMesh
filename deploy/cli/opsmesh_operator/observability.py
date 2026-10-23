"""Render optional host Alertmanager configuration from the native operator."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from opsmesh_operator.files import atomic_write


def render_alertmanager(output: Path) -> dict[str, str]:
    value = os.environ.get("OPSMESH_ALERT_WEBHOOK_URL", "").strip()
    parsed = urlsplit(value)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.scheme not in {"https", "http"}
        or (parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})
    ):
        raise ValueError("Set a valid HTTPS OPSMESH_ALERT_WEBHOOK_URL (HTTP is loopback only)")
    token = os.environ.get("OPSMESH_ALERT_WEBHOOK_BEARER_TOKEN")
    authorization = (
        "        http_config:\n"
        "          authorization:\n"
        "            type: Bearer\n"
        f"            credentials: {json.dumps(token)}\n"
        if token
        else ""
    )
    content = (
        "global:\n  resolve_timeout: 5m\n\n"
        "route:\n  receiver: opsmesh-operators\n"
        "  group_by: [alertname, service, severity]\n"
        "  group_wait: 30s\n  group_interval: 5m\n  repeat_interval: 4h\n\n"
        "receivers:\n  - name: opsmesh-operators\n    webhook_configs:\n"
        f"      - url: {json.dumps(value)}\n        send_resolved: true\n{authorization}"
    )
    atomic_write(output, content)
    return {"output": str(output)}
