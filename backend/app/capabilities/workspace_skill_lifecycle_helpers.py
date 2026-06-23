from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.capabilities.models import Skill, WorkspaceSkillInstall


def require_workspace_install(
    session: Session,
    workspace_id: UUID,
    install_id: UUID,
) -> WorkspaceSkillInstall:
    install = session.get(WorkspaceSkillInstall, install_id)
    if install is None or install.workspace_id != workspace_id:
        raise ValueError("Workspace skill install not found")
    return install


def require_installable_skill(session: Session, workspace_id: UUID, skill_id: UUID) -> Skill:
    skill = session.get(Skill, skill_id)
    if skill is None or skill.status != "active" or not can_use_skill(workspace_id, skill):
        raise ValueError("Skill not found")
    return skill


def can_use_skill(workspace_id: UUID, skill: Skill) -> bool:
    return skill.visibility == "public" or skill.owner_workspace_id == workspace_id


def copy_skill_snapshot(install: WorkspaceSkillInstall, skill: Skill) -> None:
    install.installed_key = skill.key
    install.installed_name = skill.name
    install.installed_version = skill.version
    install.installed_description = skill.description
    install.installed_capability_keys = list(skill.capability_keys)
    install.installed_manifest = dict(skill.manifest)
    install.source_owner_workspace_id = skill.owner_workspace_id
    install.source_visibility = skill.visibility
    install.source_checksum = skill_checksum(skill)


def skill_checksum(skill: Skill) -> str:
    payload = {
        "key": skill.key,
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "capability_keys": skill.capability_keys,
        "manifest": skill.manifest,
        "owner_workspace_id": str(skill.owner_workspace_id)
        if skill.owner_workspace_id is not None
        else None,
        "visibility": skill.visibility,
    }
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def require_same_skill_key(install: WorkspaceSkillInstall, skill: Skill) -> None:
    if install.installed_key != skill.key:
        raise ValueError("Skill key mismatch")


def append_skill_install_history(
    config: dict[str, object],
    install: WorkspaceSkillInstall,
    *,
    action: str,
    user_id: UUID,
) -> dict[str, object]:
    next_config = dict(config)
    lifecycle = next_config.get("_lifecycle")
    lifecycle_payload = dict(lifecycle) if isinstance(lifecycle, dict) else {}
    raw_history = lifecycle_payload.get("history")
    history = list(raw_history) if isinstance(raw_history, list) else []
    history.append(
        {
            "skill_id": str(install.skill_id),
            "installed_key": install.installed_key,
            "installed_version": install.installed_version,
            "source_checksum": install.source_checksum,
            "status": install.status,
            "recorded_at": datetime.now(UTC).isoformat(),
            "action": action,
            "user_id": str(user_id),
        }
    )
    lifecycle_payload["history"] = history[-20:]
    next_config["_lifecycle"] = lifecycle_payload
    return next_config


def config_with_lifecycle(
    config: dict[str, object],
    lifecycle_config: dict[str, object],
) -> dict[str, object]:
    next_config = dict(config)
    lifecycle = lifecycle_config.get("_lifecycle")
    if isinstance(lifecycle, dict):
        next_config["_lifecycle"] = lifecycle
    return next_config


def latest_history_skill_id(config: dict[str, object]) -> UUID | None:
    lifecycle = config.get("_lifecycle")
    if not isinstance(lifecycle, dict):
        return None
    history = lifecycle.get("history")
    if not isinstance(history, list) or not history:
        return None
    latest = history[-1]
    if not isinstance(latest, dict):
        return None
    raw_skill_id = latest.get("skill_id")
    try:
        return UUID(str(raw_skill_id))
    except (TypeError, ValueError):
        return None
