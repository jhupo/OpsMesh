"""Message, MessageSegment, Pydantic v2 validation, and MessageTemplate tests."""

from pydantic import TypeAdapter
import pytest

from opsmesh.adapters import Message, MessageSegment, MessageTemplate


class Seg(MessageSegment["Msg"]):
    @classmethod
    def get_message_class(cls) -> type["Msg"]:
        return Msg

    def __str__(self) -> str:
        if self.type == "text":
            return self.data["text"]
        return f"[{self.type}:{self.data.get('id', '')}]"

    def is_text(self) -> bool:
        return self.type == "text"

    @classmethod
    def text(cls, text: str) -> "Seg":
        return cls("text", {"text": text})

    @classmethod
    def mention(cls, uid: str) -> "Seg":
        return cls("mention", {"id": uid})


class Msg(Message[Seg]):
    @classmethod
    def get_segment_class(cls) -> type[Seg]:
        return Seg

    @staticmethod
    def _construct(msg: str):
        yield Seg.text(msg)


def test_message_operations_slicing_and_filtering():
    msg = Seg.text("hello ") + Seg.mention("u1") + " world " + Seg.mention("u2")
    assert isinstance(msg, Msg)
    assert str(msg) == "hello [mention:u1] world [mention:u2]"
    assert msg.extract_plain_text() == "hello  world "

    assert "mention" in msg
    assert msg.has("text")
    assert msg.count("mention") == 2
    assert msg.index("mention") == 1
    assert msg["mention", 0] == Seg.mention("u1")
    assert len(msg["mention", :1]) == 1
    assert len(msg.get("mention", count=1)) == 1
    assert msg.include("text").only("text")
    assert msg.exclude("mention").only("text")

    joined = Seg.text(" | ").join([Msg("a"), Seg.mention("b")])
    assert str(joined) == "a | [mention:b]"

    copied = msg.copy()
    assert copied == msg
    assert copied is not msg
    copied += "!"
    assert copied != msg


def test_message_and_segment_pydantic_v2_validation():
    seg_adapter = TypeAdapter(Seg)
    msg_adapter = TypeAdapter(Msg)

    seg = seg_adapter.validate_python({"type": "text", "data": {"text": "ok"}})
    assert seg == Seg.text("ok")

    with pytest.raises(ValueError, match="Expected dict with 'type'"):
        seg_adapter.validate_python({"data": {}})

    assert str(msg_adapter.validate_python("raw")) == "raw"
    assert msg_adapter.validate_python(
        [
            {"type": "text", "data": {"text": "a"}},
            {"type": "mention", "data": {"id": "1"}},
        ]
    ) == (Seg.text("a") + Seg.mention("1"))


def test_message_template_formatting_and_private_attr_guard():
    tmpl = Msg.template("Hi {user:mention}, status={status}, tag={tag:upper}")
    tmpl.add_format_spec(lambda v: str(v).upper(), name="upper")
    rendered = tmpl.format(user="root", status="ok", tag="v1")
    assert isinstance(rendered, Msg)
    assert str(rendered) == "Hi [mention:root], status=ok, tag=V1"

    class SecretBox:
        _secret = "top-secret"
        public = "visible"

    safe_tmpl = MessageTemplate("{box.public}")
    assert safe_tmpl.format(box=SecretBox()) == "visible"

    with pytest.raises(ValueError, match="Cannot access private attribute"):
        MessageTemplate("{box._secret}").format(box=SecretBox())

    assert (
        MessageTemplate("{box._secret}", private_getattr=True).format(box=SecretBox())
        == "top-secret"
    )
