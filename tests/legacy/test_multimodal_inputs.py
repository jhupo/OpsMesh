"""Verify native SDK inputs, persistent history, and sandbox file writes."""

import base64
import runpy
from pathlib import Path

import pytest
from agents.extensions.memory import SQLAlchemySession
from sqlalchemy.ext.asyncio import create_async_engine

from opsmesh.plugins.channels.attachments import (
    Attachment,
    AttachmentCollection,
    AttachmentError,
)
from opsmesh.plugins.channels.messages import Message, MessageSegment

inputs = runpy.run_path(
    str(
        Path(__file__).parents[2]
        / "examples/common/conversation_support/inputs.py"
    )
)
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5XcAAAAASUVORK5CYII="
)


@pytest.mark.anyio
async def test_native_image_and_pdf_history_survives_source_disposal(tmp_path):
    files = AttachmentCollection()
    picture = files.add_bytes(Attachment.create("pixel.png", "image/png"), PNG)
    document = files.add_bytes(
        Attachment.create("report.pdf", "application/pdf"), b"%PDF-1.4 test"
    )
    message = Message(
        [
            MessageSegment.text("compare"),
            MessageSegment.attachment(picture),
            MessageSegment.attachment(document),
        ]
    )
    value = await inputs["model_input"](message, files)
    parts = value[0]["content"]
    assert [part["type"] for part in parts] == [
        "input_text",
        "input_image",
        "input_file",
    ]
    assert base64.b64decode(parts[1]["image_url"].split(",", 1)[1]) == PNG
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'history.db'}"
    )
    try:
        session = SQLAlchemySession(
            "conversation", engine=engine, create_tables=True
        )
        await session.add_items(value)
        del files
        restored = SQLAlchemySession(
            "conversation", engine=engine, create_tables=True
        )
        assert await restored.get_items() == value
        await restored.clear_session()
        assert await restored.get_items() == []
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_sandbox_receives_bytes_at_generated_relative_path():
    files = AttachmentCollection()
    attachment = files.add_bytes(
        Attachment.create("../../notes.csv", "text/csv"), b"a,b\n1,2"
    )
    written = {}

    class Sandbox:
        async def write(self, path, stream):
            written[path] = stream.read()

    prompt = await inputs["sandbox_input"](
        Message(MessageSegment.attachment(attachment)), files, Sandbox()
    )
    path = next(iter(written))
    assert path.parent == Path(".")
    assert path.name.startswith("attachment-")
    assert path.suffix == ".csv"
    assert written[path] == b"a,b\n1,2"
    assert str(path) in prompt


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kind,content",
    [
        ("image/png", b"not an image"),
        ("audio/ogg", b"audio"),
        ("application/octet-stream", b"binary"),
    ],
)
async def test_regular_agent_rejects_invalid_or_unsupported_media(
    kind, content
):
    files = AttachmentCollection()
    attachment = files.add_bytes(Attachment.create("input", kind), content)
    with pytest.raises(AttachmentError):
        await inputs["model_input"](
            Message(MessageSegment.attachment(attachment)), files
        )
