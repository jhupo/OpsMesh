"""QQ application QR binding protocol; credentials never leave the host API."""

import base64
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def request(path: str, payload: dict[str, str]) -> dict[str, Any]:
    with httpx.Client(
        base_url="https://q.qq.com", timeout=10, trust_env=False, follow_redirects=False
    ) as client:
        response = client.post(path, json=payload)
        response.raise_for_status()
        body = response.json()
    if (
        not isinstance(body, dict)
        or body.get("retcode") not in (0, "0")
        or not isinstance(body.get("data"), dict)
    ):
        raise ValueError("Invalid QQ binding response")
    return dict(body["data"])


def create() -> dict[str, str]:
    key = base64.b64encode(secrets.token_bytes(32)).decode()
    data = request("/lite/create_bind_task", {"key": key})
    task_id = str(data.get("task_id") or "")
    if not task_id or len(task_id) > 500:
        raise ValueError("Invalid QQ binding task")
    query = urlencode({"task_id": task_id, "source": "opsmesh", "_wv": 2})
    return {
        "task_id": task_id,
        "key": key,
        "url": "https://q.qq.com/qqbot/openclaw/connect.html?" + query,
    }


def poll(state: dict[str, Any]) -> tuple[str, dict[str, str]]:
    data = request("/lite/poll_bind_result", {"task_id": str(state["task_id"])})
    status = int(data.get("status") or 0)
    if status == 3:
        return "expired", {}
    if status != 2:
        return "pending", {}
    encrypted = base64.b64decode(str(data["bot_encrypt_secret"]), validate=True)
    key = base64.b64decode(str(state["key"]), validate=True)
    if len(key) != 32 or len(encrypted) < 29:
        raise ValueError("Invalid QQ binding credentials")
    secret = AESGCM(key).decrypt(encrypted[:12], encrypted[12:], None).decode()
    app_id = str(data.get("bot_appid") or "")
    if not app_id or not secret:
        raise ValueError("Missing QQ application credentials")
    return "completed", {"app_id": app_id, "app_secret": secret}
