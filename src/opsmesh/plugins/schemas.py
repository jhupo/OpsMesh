from typing import Any

from opsmesh.shared.schemas import RecordView, RequestSchema


class UpdatePluginRequest(RequestSchema):
    enabled: bool


class PluginView(RecordView):
    owner_id: str
    name: str
    version: str
    manifest: dict[str, Any]
    enabled: bool
