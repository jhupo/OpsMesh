"""Exercise typed output contracts and native SDK-to-channel integration."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from opsmesh.plugins.channels import (
    Attachment,
    AttachmentCollection,
    Delivery,
    Message,
    MessageSegment,
    StreamMessageEnd,
    StreamMessageStart,
    StreamTextDelta,
)
from opsmesh.plugins.channels.api.output import MessageOutput
from opsmesh.plugins.channels.sending import normalize
from opsmesh.plugins.channels.streaming import TextBlock, message_stream

ROOT = Path(__file__).resolve().parents[2]


def example_module(filename):
    path = ROOT / "examples/common/conversation_support" / filename
    spec = importlib.util.spec_from_file_location("test_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    if filename == "files.py":
        sys.modules["conversation_support.files"] = module
    spec.loader.exec_module(module)
    return module


async def consume_stream(source, send):
    async with message_stream(source) as reader:
        async for identifier, block in reader.blocks():
            if isinstance(block, TextBlock):
                block = await block.message()
            await send(block, identifier)


Deliverables = example_module("files.py").Deliverables


@pytest.mark.anyio
async def test_fixed_files_survive_original_mutation_and_stay_scoped():
    files = AttachmentCollection()
    selected = Deliverables(files)
    original = bytearray(b"original")
    item = selected.add(
        Attachment.create("report.txt", "text/plain"), bytes(original)
    )
    selected.select(item.id)
    original[:] = b"modified"
    assert await files.read(item.id) == b"original"
    with pytest.raises(ValueError):
        Deliverables(files).select(item.id)


def test_table_shape_and_numeric_values_are_validated():
    with pytest.raises(ValueError):
        MessageSegment.table(["a"], [[1, 2]])
    with pytest.raises(ValueError):
        MessageSegment.table(["a"], [[float("nan")]])
    with pytest.raises(ValueError):
        normalize(MessageSegment("table", {"columns": ["a"], "rows": [[{}]]}))


def test_table_fallback_is_explicit_and_preserves_every_cell():
    table = Message(MessageSegment.table(["Name", "Total"], [["alpha", 3]]))
    outputs = example_module("outputs.py")
    assert outputs.render_table(table[0].data) == "1.\nName: alpha\nTotal: 3"


@pytest.mark.anyio
async def test_mixed_stream_preserves_message_and_file_order():
    files = Deliverables(AttachmentCollection())
    file = files.add(Attachment.create("report.txt", "text/plain"), b"report")
    files.select(file.id)
    closed = []

    async def source():
        try:
            yield StreamMessageStart("intro", "markdown")
            yield StreamTextDelta("intro", 1, "**report**")
            yield StreamMessageEnd("intro")
            yield Message(MessageSegment.table(["total"], [[1]]))
            yield files.message()
            yield StreamMessageStart("end")
            yield StreamTextDelta("end", 1, "done")
            yield StreamMessageEnd("end")
        finally:
            closed.append(True)

    messages = []

    async def send(message, identifier):
        messages.append(message)

    await consume_stream(source(), send)
    assert [part.type for message in messages for part in message] == [
        "markdown",
        "table",
        "file",
        "text",
    ]
    assert closed == [True]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "items",
    [
        ["legacy text"],
        [StreamTextDelta("a", 1, "bad")],
        [StreamMessageStart("a"), StreamTextDelta("a", 2, "bad")],
        [StreamMessageStart("a"), Message("nested")],
        [StreamMessageStart("a"), StreamTextDelta("a", 1, "incomplete")],
        [StreamMessageStart("a"), StreamMessageEnd("a")],
    ],
)
async def test_invalid_stream_closes_source(items):
    closed = []

    async def source():
        try:
            for item in items:
                yield item
        finally:
            closed.append(True)

    async def send(*_):
        raise AssertionError("Invalid content must not reach the sender")

    with pytest.raises((ValueError, TypeError)):
        await consume_stream(source(), send)
    assert closed == [True]


@pytest.mark.anyio
async def test_partial_receipts_do_not_overwrite_confirmed_operations():
    delivery = Delivery()
    async with delivery.operation("a", position=0) as operation:
        operation.response = {"id": "accepted"}
    with pytest.raises(TimeoutError):
        async with delivery.operation("a", position=1):
            raise TimeoutError
    assert [(r.position, r.status) for r in delivery.receipts] == [
        (0, "confirmed"),
        (1, "unknown"),
    ]


@pytest.mark.anyio
async def test_http_receipt_is_not_generated_when_a_frame_is_only_queued():
    channel = SimpleNamespace(
        owner_id="owner",
        self_id="api:owner:main",
        settings=SimpleNamespace(max_output_bytes=4096, max_output_messages=5),
        uploads=SimpleNamespace(put_many=lambda *args: None),
    )
    event = SimpleNamespace(
        user_id="u",
        attachments=AttachmentCollection(),
    )
    output = MessageOutput(channel, event)
    frames = []

    async def emit(frame):
        frames.append(frame)

    delivery = Delivery()
    output.emit = emit
    await output.send(MessageSegment.table(["total"], [[7]]), delivery=delivery)
    assert not delivery.receipts
    await output.written(frames[0])
    await output.finish_transport()
    assert [r.status for r in delivery.receipts] == ["written"]
    assert json.loads(frames[0].body.decode().split("data: ")[1])["content"][0][
        "rows"
    ] == [[7]]


@pytest.mark.anyio
@pytest.mark.parametrize("publication", ["buffered", "progressive"])
async def test_native_sdk_output_has_one_consumer_and_no_duplicate_final(
    publication,
):
    from agents import Agent, RunConfig, Runner
    from agents.testing import ModelStep, ScriptedModel, assistant_message

    output = example_module("outputs.py").ConversationOutput(
        publication=publication
    )
    result = Runner.run_streamed(
        Agent(
            name="test",
            model=ScriptedModel(
                [
                    ModelStep(output=[assistant_message("hello")]),
                ]
            ),
        ),
        "question",
        run_config=RunConfig(tracing_disabled=True),
    )
    messages = []

    async def send(message, identifier):
        messages.append(str(message))

    await consume_stream(output.stream(result), send)
    assert messages == ["hello"]
    with pytest.raises(RuntimeError):
        await anext(output.stream(result))


@pytest.mark.anyio
async def test_buffered_output_never_publishes_guardrail_rejected_text():
    from agents import (
        Agent,
        GuardrailFunctionOutput,
        RunConfig,
        Runner,
        output_guardrail,
    )
    from agents.exceptions import OutputGuardrailTripwireTriggered
    from agents.testing import ModelStep, ScriptedModel, assistant_message

    @output_guardrail
    async def reject(ctx, agent, output):
        return GuardrailFunctionOutput(
            output_info=None, tripwire_triggered=True
        )

    result = Runner.run_streamed(
        Agent(
            name="test",
            model=ScriptedModel(
                [
                    ModelStep(output=[assistant_message("private")]),
                ]
            ),
            output_guardrails=[reject],
        ),
        "question",
        run_config=RunConfig(tracing_disabled=True),
    )
    output = example_module("outputs.py").ConversationOutput()
    published = []
    with pytest.raises(OutputGuardrailTripwireTriggered):
        async for item in output.stream(result):
            published.append(item)
    assert not published


@pytest.mark.anyio
async def test_native_image_items_are_registered_once_not_tool_result_images():
    import base64

    from agents import Agent
    from agents.items import ToolCallItem, ToolCallOutputItem
    from openai.types.responses.response_output_item import ImageGenerationCall

    agent = Agent(name="test")
    image = ImageGenerationCall(
        id="image1",
        type="image_generation_call",
        status="completed",
        result=base64.b64encode(b"\x89PNG\r\n\x1a\ncontent").decode(),
    )
    item = ToolCallItem(agent=agent, raw_item=image)
    files = Deliverables(AttachmentCollection())
    output = example_module("outputs.py").ConversationOutput(files=files)
    result = SimpleNamespace(
        new_items=[
            item,
            item,
            ToolCallOutputItem(
                agent=agent,
                raw_item={
                    "type": "function_call_output",
                    "call_id": "x",
                    "output": "private",
                },
                output={"image": "private"},
            ),
        ]
    )
    output._images(result)
    assert len(files.entries) == 1


@pytest.mark.anyio
async def test_saved_files_restore_from_fixed_content_after_restart(tmp_path):
    class Filesystem:
        def data_file(self, name):
            return tmp_path / name

        async def write_file(self, path, content, **kwargs):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(
                content if isinstance(content, bytes) else content.encode()
            )

        async def read_file(self, path):
            return path.read_bytes()

    from uuid import uuid4

    module = example_module("deliveries.py")
    session_id = str(uuid4())
    filesystem = Filesystem()
    archive = module.ConversationDeliveries(filesystem, session_id, "owner")
    files = Deliverables(AttachmentCollection())
    file = files.add(Attachment.create("report.txt", "text/plain"), b"fixed")
    files.select(file.id)
    await archive.prepare(files)
    restored = Deliverables(AttachmentCollection())
    await module.ConversationDeliveries.restore(
        filesystem,
        (session_id, "owner", archive.reply_id),
        restored,
    )
    assert (
        await restored.attachments.read(restored.entries[0].attachment.id)
        == b"fixed"
    )
    with pytest.raises(PermissionError):
        await module.ConversationDeliveries.restore(
            filesystem,
            (session_id, "other", archive.reply_id),
            Deliverables(AttachmentCollection()),
        )


@pytest.mark.anyio
async def test_sandbox_publisher_fixes_bytes_and_rejects_traversal():
    from agents.tool_context import ToolContext

    module = example_module("../../sandbox/example_sandbox/tools/files.py")
    content = b"original"
    reads = []

    async def read_bounded(path, *, max_bytes):
        reads.append((path, max_bytes))
        return content

    session = SimpleNamespace(
        state=SimpleNamespace(manifest=SimpleNamespace(root="/workspace")),
        read_bounded=read_bounded,
    )
    files = Deliverables(AttachmentCollection())
    publisher = module.SandboxFiles(session, files)
    result = await publisher.tool.on_invoke_tool(
        ToolContext(
            None,
            tool_name="publish_file",
            tool_call_id="test",
            tool_arguments="{}",
        ),
        json.dumps({"path": "reports/a.txt", "description": "Report"}),
    )
    content = b"changed"
    assert await files.attachments.read(result["attachment_id"]) == b"original"
    assert reads[0][0] == Path("/workspace/reports/a.txt")
    with pytest.raises(ValueError):
        await publisher.publish_file("../secret")


@pytest.mark.anyio
async def test_api_rejects_mislabeled_media_before_uploading():
    from opsmesh.plugins.channels.api.attachments import prepare_message
    from opsmesh.plugins.channels.attachments import AttachmentError

    files = AttachmentCollection()
    attachment = files.add_bytes(
        Attachment.create("a.txt", "text/plain"), b"text"
    )
    uploaded = []
    channel = SimpleNamespace(
        owner_id="owner",
        uploads=SimpleNamespace(put_many=lambda *args: uploaded.append(args)),
    )
    event = SimpleNamespace(user_id="u", attachments=files)
    with pytest.raises(AttachmentError):
        await prepare_message(
            channel,
            event,
            Message(MessageSegment("image", {"attachment_id": attachment.id})),
        )
    assert not uploaded


@pytest.mark.anyio
async def test_approval_interruption_does_not_publish_buffered_files():
    from agents import Agent, RunConfig, Runner, function_tool
    from agents.testing import ModelStep, ScriptedModel, function_call

    @function_tool(needs_approval=True)
    def protected() -> str:
        return "approved"

    files = Deliverables(AttachmentCollection())
    item = files.add(Attachment.create("private.txt", "text/plain"), b"private")
    files.select(item.id)
    result = Runner.run_streamed(
        Agent(
            name="approval",
            model=ScriptedModel(
                [
                    ModelStep(
                        output=[
                            function_call("protected", "{}", call_id="approval")
                        ]
                    ),
                ]
            ),
            tools=[protected],
        ),
        "run",
        run_config=RunConfig(tracing_disabled=True),
    )
    output = example_module("outputs.py").ConversationOutput(files=files)
    published = []
    with pytest.raises(RuntimeError, match="Approval continuation"):
        async for item in output.stream(result):
            published.append(item)
    assert not published


@pytest.mark.anyio
@pytest.mark.parametrize("publication", ["buffered", "progressive"])
async def test_structured_sdk_output_is_validated_before_publication(
    publication,
):
    from agents import Agent, RunConfig, Runner
    from agents.testing import ModelStep, ScriptedModel, assistant_message
    from pydantic import BaseModel

    class Report(BaseModel):
        total: int

    result = Runner.run_streamed(
        Agent(
            name="report",
            output_type=Report,
            model=ScriptedModel(
                [
                    ModelStep(output=[assistant_message('{"total": 5}')]),
                ]
            ),
        ),
        "report",
        run_config=RunConfig(tracing_disabled=True),
    )
    output_type = example_module("outputs.py").ConversationOutput
    if publication == "progressive":
        output = output_type(publication=publication)
        with pytest.raises(ValueError, match="buffered"):
            async for _item in output.stream(result):
                pytest.fail("Structured tokens must not be published")
    else:
        output = output_type(
            structured=lambda report: Message(
                MessageSegment.table(["Total"], [[report.total]])
            )
        )
        messages = [item async for item in output.stream(result)]
        assert messages[0][0].data["rows"] == [[5]]


@pytest.mark.anyio
async def test_archive_cleanup_preserves_original_source_failure(tmp_path):
    from uuid import uuid4

    class Source:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise ValueError("model failed")

        async def aclose(self):
            raise RuntimeError("cleanup failed")

    archive = example_module("deliveries.py").ConversationDeliveries(
        SimpleNamespace(data_file=lambda name: tmp_path / name),
        str(uuid4()),
        "owner",
    )
    with pytest.raises(ValueError, match="model failed") as failure:
        async for _item in archive.messages(
            Source(), Deliverables(AttachmentCollection())
        ):
            pytest.fail("Failed output must not be sent")
    assert "cleanup failed" in failure.value.__notes__[0]


@pytest.mark.anyio
async def test_output_budget_rejection_does_not_publish_downloads():
    from opsmesh.plugins.channels.api.attachments import AttachmentUploads
    from opsmesh.plugins.channels.api.errors import HTTPError
    from opsmesh.plugins.channels.attachments import AttachmentSettings

    files = AttachmentCollection()
    file = files.add_bytes(Attachment.create("file.txt", "text/plain"), b"file")
    uploads = AttachmentUploads(AttachmentSettings())
    output = MessageOutput(
        SimpleNamespace(
            owner_id="owner",
            self_id="api:owner:main",
            settings=SimpleNamespace(max_output_bytes=1, max_output_messages=4),
            uploads=uploads,
        ),
        SimpleNamespace(user_id="user", attachments=files),
    )
    with pytest.raises(HTTPError):
        await output.send(Message(MessageSegment.attachment(file)))
    assert (uploads._size, len(uploads._uploads), output.messages) == (0, 0, [])


@pytest.mark.anyio
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("limit,accepted", [(9, False), (10, True)])
async def test_content_budget_is_independent_of_response_encoding(
    streamed, limit, accepted
):
    from opsmesh.plugins.channels.api.errors import HTTPError

    output = MessageOutput(
        SimpleNamespace(
            owner_id="owner",
            self_id="api:owner:main",
            settings=SimpleNamespace(
                max_output_bytes=limit, max_output_messages=4
            ),
        ),
        SimpleNamespace(
            user_id="user",
            attachments=AttachmentCollection(),
        ),
    )
    frames = []

    async def emit(frame):
        frames.append(frame)

    if streamed:
        output.emit = emit

    async def source():
        yield StreamMessageStart("a")
        for index in range(10):
            yield StreamTextDelta("a", index + 1, "x")
        yield StreamMessageEnd("a")

    if accepted:
        await output.stream(source())
        assert output._bytes == 10
    else:
        with pytest.raises(HTTPError):
            await output.stream(source())


@pytest.mark.anyio
async def test_explicit_native_stream_is_not_limited_by_whole_block_time(
    monkeypatch,
):
    import anyio

    from opsmesh.plugins.channels import streaming

    fail_after = anyio.fail_after
    monkeypatch.setattr(
        anyio,
        "fail_after",
        lambda seconds, **kw: fail_after(
            0.06 if seconds == 60 else seconds, **kw
        ),
    )

    async def source():
        yield StreamMessageStart("a", "markdown")
        for index in range(4):
            await anyio.sleep(0.025)
            yield StreamTextDelta("a", index + 1, "x")
        yield StreamMessageEnd("a")

    async def card(block, identifier):
        return "".join([chunk async for chunk in block.chunks()])

    async def no_send(*args):
        pytest.fail("A supported native stream must not become a complete send")

    assert await streaming.send_stream(source(), no_send, card=card) == ["xxxx"]


def test_example_selects_buffering_and_text_for_a_nonstreaming_bot():
    output = example_module("outputs.py").ConversationOutput(
        format="markdown",
        publication="progressive",
        channel=SimpleNamespace(
            send_types=frozenset({"text", "file"}), stream_formats=frozenset()
        ),
    )
    assert (output.format, output.publication) == ("text", "buffered")
    message = Message(MessageSegment.table(["Name"], [["A"]]))
    assert str(output._present(message)) == "1.\nName: A"
