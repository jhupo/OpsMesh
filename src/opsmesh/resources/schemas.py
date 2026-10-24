from typing import Literal

from pydantic import Field

from opsmesh.shared.schemas import RecordView, RequestSchema, ResourceView

StorageKind = Literal["workspace", "sandbox"]


class CreateWorkspaceRequest(RequestSchema):
    name: str = Field(min_length=1, max_length=100)


class FileEntryView(ResourceView):
    path: str
    directory: bool
    size: int


class WorkspaceView(RecordView):
    owner_id: str
    name: str
