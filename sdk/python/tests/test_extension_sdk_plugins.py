import json
import zipfile
from pathlib import Path

import pytest
from opsmesh_sdk import PluginContext
from opsmesh_sdk.manifest import PluginManifest
from opsmesh_sdk.packages import build_package, inspect_package


def source(root: Path) -> Path:
    directory = root / "example"
    directory.mkdir()
    (directory / "plugin.yaml").write_text(
        json.dumps(
            {
                "id": "example",
                "version": "0.1.0",
                "name": "Example",
                "kind": "tool",
                "entry": "main.py",
            }
        )
    )
    (directory / "main.py").write_text("raise RuntimeError('packaging must not import code')")
    return directory


def test_build_and_check_do_not_import_plugin(tmp_path):
    archive = tmp_path / "example.zip"
    manifest = build_package(source(tmp_path), archive)
    assert manifest.id == "example"
    assert inspect_package(archive) == manifest


@pytest.mark.parametrize("name", ["../escape", "/absolute", "opsmesh/config.py", "a\\b"])
def test_package_rejects_unsafe_members(tmp_path, name):
    archive = tmp_path / "example.zip"
    build_package(source(tmp_path), archive)
    with zipfile.ZipFile(archive, "a") as package:
        item = zipfile.ZipInfo()
        item.filename = name
        item.orig_filename = name
        package.writestr(item, "unsafe")
    with pytest.raises(ValueError, match="Unsafe"):
        inspect_package(archive)


def test_registration_rejects_invalid_and_duplicate_names(tmp_path):
    context = PluginContext(
        manifest=PluginManifest(id="example", version="0.1.0", name="Example", kind="tool"),
        source_path=tmp_path,
        data_directory=tmp_path / "data",
        config={"prefix": "hello"},
    )

    async def echo(call):
        return call.arguments

    with pytest.raises(ValueError, match="identifiers"):
        context.tool("invalid-name", echo)
    context.tool("echo", echo)
    with pytest.raises(ValueError, match="unique"):
        context.tool("echo", echo)
    with pytest.raises(TypeError):
        context.config["prefix"] = "overwrite"


def test_message_filter_registration_is_explicit_and_bounded(tmp_path):
    context = PluginContext(
        manifest=PluginManifest(
            id="example", version="0.1.0", name="Example", kind="message_filter"
        ),
        source_path=tmp_path,
        data_directory=tmp_path / "data",
    )

    async def moderate(message, media):
        from opsmesh_sdk import MessageFilterResult

        return MessageFilterResult(action="allow")

    with pytest.raises(TypeError, match="async"):
        context.message_filter("check", lambda message, media: True)
    with pytest.raises(ValueError, match="timeout"):
        context.message_filter("check", moderate, timeout_seconds=0)
    context.message_filter("check", moderate, timeout_seconds=2)
    assert context.contributions()["message_filter"]["check"].handler is moderate
    with pytest.raises(ValueError, match="unique"):
        context.message_filter("check", moderate)
