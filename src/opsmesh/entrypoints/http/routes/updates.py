"""Authorized release checks and staging; installation runs from Linux maintenance CLI."""

from fastapi import APIRouter, Request

from opsmesh.framework.typing import OpsMeshContract
from opsmesh.maintenance.updates import ReleaseUpdate, ReleaseUpdateService

from ..security import CurrentUser, authorize, get_application

router = APIRouter(prefix="/operations/updates", tags=["updates"])


class DownloadUpdate(OpsMeshContract):
    version: str | None = None


@router.get("")
async def check(request: Request, user: CurrentUser, version: str | None = None) -> ReleaseUpdate:
    await authorize(request, user, "update:manage", "platform")
    return await ReleaseUpdateService(get_application(request).services.http_client).check(version)


@router.post("/download")
async def download(body: DownloadUpdate, request: Request, user: CurrentUser) -> ReleaseUpdate:
    await authorize(request, user, "update:manage", "platform")
    app = get_application(request)
    updater = ReleaseUpdateService(app.services.http_client)
    release = await updater.check(body.version)
    await updater.download(release, app.settings.data_directory / "updates")
    return release
