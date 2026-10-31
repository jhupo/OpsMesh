"""Stable bounded keys for bot configuration and deduplication records."""

import hashlib
import json


def bot_record_key(kind: str, *parts: str) -> str:
    digest = hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()
    return f"{kind}:{digest}"
