import json
from hashlib import sha256

from backend.app.api.schemas.exports import WorkspaceImportRequest


def _metadata_preview_token(request: WorkspaceImportRequest) -> str:
    payload = {
        "export": request.export.model_dump(mode="json"),
        "import_agents": request.import_agents,
        "import_teams": request.import_teams,
        "import_tasks": request.import_tasks,
        "import_runtime_spaces": request.import_runtime_spaces,
        "import_skill_installs": request.import_skill_installs,
        "max_items_per_collection": request.max_items_per_collection,
        "name_prefix": request.name_prefix,
    }
    content = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(content.encode("utf-8")).hexdigest()
