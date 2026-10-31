"""Read legacy persisted connection fields into the neutral account representation."""

from opsmesh.framework.typing import JsonData


def migrate_account_configuration(data: JsonData) -> JsonData:
    if data.get("adapter", "onebot_v11") != "onebot_v11" or "settings" in data:
        return data
    migrated = dict(data)
    migrated["adapter"] = "onebot_v11"
    migrated["settings"] = {
        name: migrated.pop(name)
        for name in (
            "transport",
            "api_base_url",
            "websocket_url",
            "reconnect_seconds",
            "file_upload",
        )
        if name in migrated and migrated[name] is not None
    }
    migrated["credentials"] = {
        name.removesuffix("_ref"): migrated.pop(name)
        for name in ("access_token_ref", "webhook_secret_ref")
        if name in migrated and migrated[name] is not None
    }
    # Older persisted rows serialize absent optional fields as null.
    for name in ("api_base_url", "websocket_url", "access_token_ref", "webhook_secret_ref"):
        migrated.pop(name, None)
    return migrated
