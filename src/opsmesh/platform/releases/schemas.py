
from pydantic import (
    BaseModel,
)


class AdminReleaseAssetResponse(BaseModel):
    name: str
    browser_download_url: str
    size: int
    content_type: str | None = None
    digest: str | None = None


class AdminReleaseVersionResponse(BaseModel):
    version: str
    tag: str
    commit: str | None = None


class AdminReleaseUpdateCheckResponse(BaseModel):
    current: AdminReleaseVersionResponse
    latest: AdminReleaseVersionResponse | None
    update_available: bool
    release_url: str | None
    assets: list[AdminReleaseAssetResponse]
    cached: bool
