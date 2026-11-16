"""Example-owned immutable files and optional delivery history."""

import json
from dataclasses import asdict
from hashlib import sha256
from uuid import UUID, uuid4

import anyio

from conversation_support.files import Deliverables
from opsmesh.plugins.channels import Attachment, Delivery


class ConversationDeliveries:
    """Persist file replies in the calling plugin's filesystem space."""

    def __init__(self, filesystem, session_id: str, owner_id: str):
        """Borrow the started filesystem plugin, not a request transaction."""
        self.filesystem = filesystem
        self.session_id = str(UUID(session_id))
        self.owner_id = owner_id
        self.reply_id = uuid4().hex
        self.root = filesystem.data_file(
            f"deliverables/{self.session_id}/{self.reply_id}"
        )
        self.manifest = None
        self._message_count = 0
        self._produced = False
        self._lock = anyio.Lock()
        self.delivery = Delivery(self.record, attempt_id=self.reply_id)

    async def prepare(self, files: Deliverables) -> None:
        """Save actual content and send intent before exposing references."""
        if self.manifest is not None or not files.entries:
            return
        entries = []
        seen = set()
        try:
            for item in files.entries:
                attachment = item.attachment
                if attachment.id not in seen:
                    content = await files.attachments.read(attachment.id)
                    if sha256(content).hexdigest() != item.checksum:
                        raise ValueError("Deliverable content changed")
                    await self.filesystem.write_file(
                        self.root / "files" / attachment.id,
                        content,
                        replace=False,
                    )
                    seen.add(attachment.id)
                entries.append(
                    {
                        **asdict(attachment),
                        "checksum": item.checksum,
                        "description": item.description,
                    }
                )
            self.manifest = {
                "reply_id": self.reply_id,
                "session_id": self.session_id,
                "owner_id": self.owner_id,
                "files": entries,
                "attempt": {"id": self.reply_id, "status": "unknown"},
                "receipts": [
                    self._receipt_data(receipt)
                    for receipt in self.delivery.receipts
                ],
            }
            await self._save()
        except BaseException as error:
            self.manifest = None
            if self.root.exists():
                with anyio.CancelScope(shield=True):
                    try:
                        await self.filesystem.delete(self.root)
                    except Exception as cleanup_error:
                        error.add_note(
                            f"File cleanup failed: {cleanup_error!r}"
                        )
            raise

    async def record(self, receipt) -> None:
        """Persist confirmed operations without erasing unresolved sends."""
        if self.manifest is None:
            return
        async with self._lock:
            self.manifest["receipts"].append(self._receipt_data(receipt))
            await self._save()

    @staticmethod
    def _receipt_data(receipt):
        # Vendor payloads may contain expiring URLs or credentials.
        record = asdict(receipt)
        record.pop("response")
        return record

    async def _save(self) -> None:
        receipts = self.manifest["receipts"]
        completed = {
            item["message_id"]
            for item in receipts
            if item["status"] in {"written", "confirmed"}
        }
        if (
            self._produced
            and len(completed) == self._message_count
            and all(
                item["status"] in {"written", "confirmed"} for item in receipts
            )
        ):
            self.manifest["attempt"]["status"] = (
                "written"
                if any(item["status"] == "written" for item in receipts)
                else "confirmed"
            )
        await self.filesystem.write_file(
            self.root / "manifest.json",
            json.dumps(self.manifest, ensure_ascii=False),
        )

    async def messages(self, source, files: Deliverables):
        """Save selected files before yielding their message."""
        from opsmesh.plugins.channels.messages import (
            Message,
            StreamMessageStart,
        )
        from opsmesh.plugins.channels.sending import close_source

        failure = None
        try:
            async for item in source:
                if isinstance(item, (Message, StreamMessageStart)):
                    self._message_count += 1
                if isinstance(item, Message) and any(
                    part.type in {"image", "file", "audio", "video"}
                    for part in item
                ):
                    await self.prepare(files)
                yield item
            self._produced = True
            if self.manifest is not None:
                async with self._lock:
                    await self._save()
        except BaseException as error:
            failure = error
            raise
        finally:
            try:
                await close_source(source)
            except BaseException as cleanup_error:
                if failure is None:
                    raise
                failure.add_note(f"Output cleanup failed: {cleanup_error!r}")

    @classmethod
    async def history(cls, filesystem, session_id: str, owner_id: str):
        """List saved replies after the caller checks session access."""
        root = filesystem.data_file(f"deliverables/{UUID(session_id)}")
        if not root.exists():
            return []
        rows = []
        for path in sorted(root.glob("*/manifest.json"), reverse=True)[:100]:
            value = json.loads(await filesystem.read_file(path))
            if (
                value["owner_id"] == owner_id
                and value["session_id"] == session_id
            ):
                rows.append(value)
        return rows

    @classmethod
    async def restore(cls, filesystem, request, files: Deliverables):
        """Check ownership and restore fresh request-scoped handles."""
        session_id, owner_id, reply_id = request
        root = filesystem.data_file(
            f"deliverables/{UUID(session_id)}/{UUID(reply_id).hex}"
        )
        value = json.loads(await filesystem.read_file(root / "manifest.json"))
        if value["owner_id"] != owner_id or value["session_id"] != session_id:
            raise PermissionError("Delivery not available")
        restored = {}
        for item in value["files"]:
            if item["id"] in restored:
                files.select(
                    restored[item["id"]], description=item["description"]
                )
                continue
            content = await filesystem.read_file(
                root / "files" / UUID(item["id"]).hex
            )
            if sha256(content).hexdigest() != item["checksum"]:
                raise ValueError("Stored deliverable content changed")
            attachment = files.add(
                Attachment.create(
                    item["name"], item["media_type"], len(content)
                ),
                content,
            )
            files.select(attachment.id, description=item["description"])
            restored[item["id"]] = attachment.id
        return files.message()
