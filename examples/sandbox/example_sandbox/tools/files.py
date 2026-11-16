"""Publish completed files through the configured native sandbox session."""

import mimetypes
from pathlib import Path, PurePosixPath

import anyio
from agents import function_tool
from agents.sandbox.session.sandbox_session import SandboxSession
from conversation_support.files import Deliverables

from opsmesh.plugins.channels import Attachment


class SandboxFiles:
    """Bind a native tool to this turn, not a shared Agent or user context."""

    def __init__(self, session: SandboxSession, files: Deliverables):
        """Use the example's Linux backend and its workspace-only manifest."""
        self.session = session
        self.files = files
        self._lock = anyio.Lock()
        self.tool = function_tool(
            self.publish_file, failure_error_function=None
        )

    async def publish_file(self, path: str, description: str = "") -> dict:
        """Select a completed workspace file for the user's reply.

        Args:
            path: Workspace-relative path to a completed regular file.
                Finish writing before publishing; do not modify it concurrently.
            description: Optional user-visible explanation of the file.

        Returns:
            An attachment identifier, file name, size and content checksum.
        """
        relative = PurePosixPath(path)
        if (
            not path
            or relative.is_absolute()
            or ".." in relative.parts
            or "\\" in path
        ):
            raise ValueError("Publish a relative file inside the workspace")
        # UnixLocalSandbox has no extra grants in this example.
        # Its bounded reader rejects symlinks and nonregular files.
        target = Path(self.session.state.manifest.root).joinpath(
            *relative.parts
        )
        async with self._lock:
            limit = self.files.attachments.settings.max_file_bytes
            content = await self.session.read_bounded(
                target, max_bytes=limit + 1
            )
            if len(content) > limit:
                raise ValueError("Published file exceeds the size limit")
            media_type = (
                mimetypes.guess_type(relative.name)[0]
                or "application/octet-stream"
            )
            attachment = self.files.add(
                Attachment.create(relative.name, media_type, len(content)),
                content,
            )
            self.files.select(attachment.id, description=description)
            return {
                "attachment_id": attachment.id,
                "name": attachment.name,
                "size": len(content),
                "checksum": self.files.get(attachment.id).checksum,
            }
