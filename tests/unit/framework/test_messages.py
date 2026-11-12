import pytest

from opsmesh.framework.adapters import Message, MessageSegment
from opsmesh.framework.messages.template import MessageTemplate


class TextSegment(MessageSegment):
    @classmethod
    def get_message_class(cls):
        return TextMessage

    def __str__(self):
        return self.data["text"]

    def is_text(self):
        return self.type == "text"


class TextMessage(Message[TextSegment]):
    @classmethod
    def get_segment_class(cls):
        return TextSegment

    @staticmethod
    def _construct(message):
        yield TextSegment("text", {"text": message})


@pytest.fixture
def message():
    return TextMessage(
        [
            TextSegment("text", {"text": "first"}),
            TextSegment("image", {"text": "image"}),
            TextSegment("text", {"text": "second"}),
            TextSegment("text", {"text": "third"}),
        ]
    )


@pytest.mark.parametrize(
    ("bounds", "expected"),
    [((), 0), ((1,), 2), ((-2,), 2), ((1, 3), 2), ((-1, 4), 3)],
)
def test_type_index_respects_list_bounds(message, bounds, expected):
    assert message.index("text", *bounds) == expected


@pytest.mark.parametrize("bounds", [(1, 2), (3, 2), (4,), (0, 0)])
def test_type_index_raises_when_range_has_no_match(message, bounds):
    with pytest.raises(ValueError):
        message.index("text", *bounds)


def test_segment_index_preserves_value_matching(message):
    assert message.index(TextSegment("text", {"text": "second"}), 1) == 2


def test_selection_and_copy_preserve_message_behavior(message):
    copied = message.copy()
    copied[0].data["text"] = "changed"
    assert (
        str(message["text", 1:]),
        str(message[1:3]),
        message.extract_plain_text(),
        str(message[0]),
    ) == ("secondthird", "imagesecond", "firstsecondthird", "first")


@pytest.mark.parametrize(
    ("template", "values", "expected"),
    [
        ("{items[0]}", {"items": ["first", "second"]}, "first"),
        ("{items[1][name]}", {"items": [{}, {"name": "second"}]}, "second"),
        ("{value[key]}", {"value": {"key": "item"}}, "item"),
    ],
)
def test_template_resolves_indexed_fields(template, values, expected):
    assert MessageTemplate(template).format(**values) == expected


@pytest.mark.parametrize("template", ["{value.__class__}", "{value[_secret]}"])
def test_template_rejects_private_lookup(template):
    with pytest.raises(ValueError, match="private attribute"):
        MessageTemplate(template).format(value={"_secret": "hidden"})


def test_template_allows_explicit_private_lookup():
    assert (
        MessageTemplate("{value[_secret]}", private_getattr=True).format(
            value={"_secret": "allowed"}
        )
        == "allowed"
    )
