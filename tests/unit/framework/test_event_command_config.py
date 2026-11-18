from types import ModuleType, SimpleNamespace

import pytest

from opsmesh.config import Config
from opsmesh.messages import Message, MessageSegment
from opsmesh.plugins.manager import PluginManager
from opsmesh.plugins.model import Plugin, PluginStatus
from opsmesh.plugins.registry import plugin_scope
from opsmesh.rules.checks import TrieRule, command, shell_command


class TextSegment(MessageSegment):
    @classmethod
    def get_message_class(cls):
        return TextMessage

    def __str__(self):
        return self.data.get("text", "")

    def is_text(self):
        return self.type == "text"


class TextMessage(Message[TextSegment]):
    @classmethod
    def get_segment_class(cls):
        return TextSegment

    @staticmethod
    def _construct(text):
        yield TextSegment("text", {"text": text})


@pytest.fixture
def owner():
    plugins = []

    def create(*, status=PluginStatus.LOADED):
        name = f"prefix_probe_{len(plugins)}"
        plugin = Plugin(
            name, ModuleType(name), name, PluginManager(), status=status
        )
        plugins.append(plugin)
        return plugin

    yield create
    for plugin in reversed(plugins):
        for cleanups in (plugin.activation_cleanups, plugin.cleanups):
            while cleanups:
                cleanups.pop()()


def config(start, sep):
    return Config(command_start={start}, command_sep={sep})


def parse(settings, *parts):
    message = TextMessage(
        [
            TextSegment("text", {"text": part})
            if isinstance(part, str)
            else part
            for part in parts
        ]
    )
    event = SimpleNamespace(
        get_type=lambda: "message", get_message=lambda: message
    )
    return TrieRule.get_value(event, {}, config=settings)


def test_declare_before_config_and_match_per_dispatch(owner):
    with plugin_scope(owner()):
        command("prefix_probe", "prefix_alias")
        shell_command(("prefix_probe", "long"))

    dot = config("/", ".")
    hyphen = config("!", "-")
    slash_hyphen = config("/", "-")
    image = TextSegment("image", {})
    info = parse(dot, "  /prefix_probe.long", "  ", " value", image)
    assert info == {
        "command": ("prefix_probe", "long"),
        "raw_command": "/prefix_probe.long",
        "command_arg": TextMessage(
            [TextSegment("text", {"text": "value"}), image]
        ),
        "command_start": "/",
        "command_whitespace": "   ",
    }
    assert parse(hyphen, "!prefix_probe-long")["command"] == (
        "prefix_probe",
        "long",
    )
    assert parse(hyphen, "/prefix_probe.long")["command"] is None
    assert parse(dot, "!prefix_probe-long")["command"] is None
    assert parse(slash_hyphen, "/prefix_probe-long")["command"] == (
        "prefix_probe",
        "long",
    )
    assert parse(slash_hyphen, "/prefix_probe.long")["command"] == (
        "prefix_probe",
    )
    assert parse(hyphen, "!prefix_probe.long")["command"] == ("prefix_probe",)
    assert parse(dot, "/prefix_alias")["command"] == ("prefix_alias",)
    assert parse(dot, image, "/prefix_alias")["command"] is None


def test_cleanup_refreshes_warm_tries_and_preserves_other_owners(owner):
    dot = config("/", ".")
    bang = config("!", ".")
    assert parse(dot, "/prefix_probe.long")["command"] is None

    first = owner()
    with plugin_scope(first):
        command(("prefix_probe", "long"), "prefix_probe")
    second = owner(status=PluginStatus.STARTING)
    with plugin_scope(second):
        shell_command("prefix_probe.long", "prefix_probe")

    assert parse(dot, "/prefix_probe.long")["command"] == (
        "prefix_probe",
        "long",
    )
    assert parse(bang, "!prefix_probe.long")["command"] == (
        "prefix_probe",
        "long",
    )
    first.cleanups.pop()()
    assert parse(dot, "/prefix_probe.long")["command"] == ("prefix_probe.long",)
    assert parse(bang, "!prefix_probe")["command"] == ("prefix_probe",)

    second.activation_cleanups.pop()()
    assert parse(dot, "/prefix_probe.long")["command"] is None
    assert parse(bang, "!prefix_probe")["command"] is None
