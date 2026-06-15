from backend.app.runs.models import AgentRun


def authorization_snapshot(run: AgentRun) -> dict[str, object]:
    run_input = run.input if isinstance(run.input, dict) else {}
    snapshot = run_input.get("authorization_snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def snapshot_audit_metadata(snapshot: dict[str, object]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    version = snapshot.get("version")
    if isinstance(version, int):
        metadata["authorization_snapshot_version"] = version
    for key in ("workspace_id", "task_id", "task_step_id", "agent_profile_id", "runtime_space_id"):
        value = snapshot.get(key)
        if value is None or isinstance(value, str):
            metadata[f"snapshot_{key}"] = value

    installed_skills = snapshot.get("installed_skills")
    if isinstance(installed_skills, list):
        metadata["snapshot_installed_skills"] = [
            {
                "install_id": item.get("install_id"),
                "installed_key": item.get("installed_key"),
                "installed_version": item.get("installed_version"),
                "source_checksum": item.get("source_checksum"),
                "source_visibility": item.get("source_visibility"),
            }
            for item in installed_skills
            if isinstance(item, dict)
        ]

    raw_tools = snapshot.get("allowed_tools")
    if isinstance(raw_tools, list):
        metadata["snapshot_allowed_tools"] = [tool for tool in raw_tools if isinstance(tool, str)]
    return metadata
