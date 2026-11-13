"""Authenticated, bounded uploads scoped to a consuming plugin and user."""

import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Annotated
from urllib.parse import quote

from fastapi import Depends, Request
from starlette.responses import Response

from opsmesh.messages import Message

from ..attachments import (
    Attachment,
    AttachmentCollection,
    AttachmentError,
    AttachmentSettings,
)
from ..requests import read_body
from .auth import Authenticator, Principal
from .errors import HTTPError
from .responses import json_response

if TYPE_CHECKING:
    from .channel import APIChannel
    from .events import HTTPEvent


@dataclass(frozen=True)
class _Upload:
    attachment: Attachment
    content: bytes
    scope: tuple[str, str]
    expires_at: float


class AttachmentUploads:
    """Keep uploads until expiration or shutdown; no process-wide registry."""

    def __init__(self, settings: AttachmentSettings) -> None:
        """Own the cache within one API listener lifecycle."""
        self.settings = settings
        self._uploads: dict[str, _Upload] = {}
        self._size = 0

    def clear(self) -> None:
        """Release all temporary uploads at shutdown."""
        self._uploads.clear()
        self._size = 0

    def _expire(self) -> None:
        now = time.monotonic()
        for key, value in tuple(self._uploads.items()):
            if value.expires_at <= now:
                self._size -= len(value.content)
                del self._uploads[key]

    def put(
        self, scope: tuple[str, str], attachment: Attachment, content: bytes
    ) -> Attachment:
        """Store an upload without evicting another active upload."""
        self.put_many(scope, [(attachment, content)])
        return attachment

    def put_many(
        self, scope: tuple[str, str], files: list[tuple[Attachment, bytes]]
    ) -> None:
        """Commit an entire reply only after all cache checks pass."""
        self._expire()
        if any(
            len(content) > self.settings.max_file_bytes for _, content in files
        ):
            raise AttachmentError("Attachment is too large")
        if (
            self._size + sum(len(content) for _, content in files)
            > self.settings.max_cache_bytes
            or len(self._uploads) + len(files) > self.settings.max_cache_entries
        ):
            raise HTTPError(503, "Attachment upload capacity exhausted")
        identifiers = {attachment.id for attachment, _ in files}
        if len(identifiers) != len(files) or identifiers.intersection(
            self._uploads
        ):
            raise AttachmentError("Duplicate upload identifier")
        expires_at = time.monotonic() + self.settings.ttl_seconds
        for attachment, content in files:
            self._uploads[attachment.id] = _Upload(
                attachment, content, scope, expires_at
            )
            self._size += len(content)

    def get(self, scope: tuple[str, str], attachment_id: str) -> _Upload:
        """Require both the original principal and consuming plugin."""
        self._expire()
        upload = self._uploads.get(attachment_id)
        if upload is None or upload.scope != scope:
            raise HTTPError(404, "Attachment not found")
        return upload

    def resolve(
        self,
        scope: tuple[str, str],
        attachment_id: str,
        files: AttachmentCollection,
    ) -> Attachment:
        """Pin an authorized upload in the request before dispatch."""
        upload = self.get(scope, attachment_id)
        if attachment_id in files:
            return files.get(attachment_id)
        return files.receive_bytes(upload.attachment, upload.content)


def mount(channel: "APIChannel") -> None:
    """Expose file transfer separately from plugin-owned chat operations."""

    async def authorize(request: Request) -> Principal:
        from opsmesh.plugins.registry import get_plugin

        if not channel.started or channel._authenticator is None:
            raise HTTPError(503, "channel is not ready")
        principal = channel._authenticator.authenticate(request)
        Authenticator.authorize(principal, "invoke")
        plugin = get_plugin(request.path_params["plugin_id"])
        if plugin is None or not plugin.ready:
            raise HTTPError(404, "Plugin not available")
        return principal

    async def upload(
        request: Request, principal: Annotated[Principal, Depends(authorize)]
    ) -> Response:
        with_scope = (request.path_params["plugin_id"], principal.caller.id)
        try:
            content = await read_body(
                request, channel.settings.attachments.max_file_bytes
            )
            attachment = Attachment.create(
                request.query_params.get("name", "attachment"),
                request.headers.get("content-type", "application/octet-stream"),
                len(content),
            )
            channel.uploads.put(with_scope, attachment, content)
        except AttachmentError as error:
            raise HTTPError(422, str(error)) from None
        return json_response(asdict(attachment), status_code=201)

    async def download(
        request: Request, principal: Annotated[Principal, Depends(authorize)]
    ) -> Response:
        scope = (request.path_params["plugin_id"], principal.caller.id)
        upload = channel.uploads.get(
            scope, request.path_params["attachment_id"]
        )
        return Response(
            upload.content,
            media_type=upload.attachment.media_type,
            headers={
                "content-disposition": "attachment; filename*=UTF-8''"
                + quote(upload.attachment.name, safe=""),
                "x-content-type-options": "nosniff",
                "cache-control": "no-store",
            },
        )

    for method, path, handler in (
        ("POST", "/attachments/{plugin_id}", upload),
        ("GET", "/attachments/{plugin_id}/{attachment_id}", download),
    ):
        channel.app.add_api_route(path, handler, methods=[method])
        channel._mounted[(method, path)] = channel.app.router.routes[-1]


async def encode_message(
    channel: "APIChannel", event: "HTTPEvent", message: Message
) -> list[dict]:
    """Turn output files into authenticated, expiring download references."""
    if not event.installation_id:
        raise HTTPError(422, "Attachment reply requires an owned route")
    parts = []
    uploads: list[tuple[Attachment, bytes]] = []
    identifiers: set[str] = set()
    for segment in message:
        if segment.is_text():
            parts.append({"type": "text", "text": str(segment)})
        elif segment.type in {"image", "file", "audio", "video"}:
            original = event.attachments.get(segment.data["attachment_id"])
            content = await event.attachments.read(original.id)
            attachment = Attachment.create(
                original.name, original.media_type, len(content)
            )
            identifiers.add(original.id)
            uploads.append((attachment, content))
            parts.append(
                {
                    "type": segment.type,
                    "attachment": asdict(attachment),
                    "download_url": (
                        f"/attachments/{event.installation_id}/{attachment.id}"
                    ),
                }
            )
        else:
            raise AttachmentError("Unsupported API output segment")
    event.attachments.validate_message(identifiers)
    channel.uploads.put_many((event.installation_id, event.user_id), uploads)
    return parts
