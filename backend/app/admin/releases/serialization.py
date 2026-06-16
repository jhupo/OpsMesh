from __future__ import annotations

import json

from backend.app.admin.releases.models import ReleaseAsset, ReleaseUpdateCheck, ReleaseVersion


def release_update_check_from_json(data: str) -> ReleaseUpdateCheck:
    payload = json.loads(data)
    return ReleaseUpdateCheck(
        current=ReleaseVersion(**payload["current"]),
        latest=ReleaseVersion(**payload["latest"]) if payload.get("latest") else None,
        update_available=bool(payload["update_available"]),
        release_url=payload.get("release_url"),
        assets=[ReleaseAsset(**asset) for asset in payload.get("assets", [])],
        cached=bool(payload.get("cached", False)),
    )
