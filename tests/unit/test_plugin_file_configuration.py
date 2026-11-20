"""File-backed consumer configuration and the existing global fallback."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"


def run_in_host(tmp_path, code):
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        timeout=20,
        env={**os.environ, "PYTHONPATH": str(SRC), "MODEL": "from-env"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_same_model_is_scoped_to_each_consumer(tmp_path):
    (tmp_path / "shared_model.py").write_text(
        "from pydantic import BaseModel\n"
        "class Settings(BaseModel):\n    model: str\n",
        encoding="utf8",
    )
    for name in ("consumer_a", "consumer_b"):
        package = tmp_path / name
        package.mkdir()
        (package / "settings.json").write_text(
            json.dumps({"config": {"model": name}, "dependencies": {}}),
            encoding="utf8",
        )
        (package / "settings_reader.py").write_text(
            "from opsmesh import get_plugin_config\n"
            "from shared_model import Settings\n"
            "def get_settings():\n    return get_plugin_config(Settings)\n",
            encoding="utf8",
        )
        (package / "__init__.py").write_text(
            """
from pathlib import Path
from opsmesh import get_driver
from opsmesh.plugin import PluginMetadata
from shared_model import Settings
from .settings_reader import get_settings
__plugin_meta__ = PluginMetadata(
    name=__name__, description="", usage="",
    config=Settings, config_file="settings.json",
)
@get_driver().on_startup
async def observe():
    get_settings().model = "mutated-copy"
    Path(__name__ + ".seen").write_text(get_settings().model, encoding="utf8")
""",
            encoding="utf8",
        )
    run_in_host(
        tmp_path,
        """
from pathlib import Path
import opsmesh
opsmesh.init(driver="~none")
for name in ("consumer_a", "consumer_b"):
    plugin = opsmesh.load_plugin(name)
    assert plugin is not None, name
    plugin.manager.enable_plugin(plugin)
driver = opsmesh.get_driver()
async def finish():
    pass
driver._listen_exit = finish
opsmesh.run()
for name in ("consumer_a", "consumer_b"):
    assert Path(name + ".seen").read_text(encoding="utf8") == name
Path("consumer_a/settings.json").write_text(
    '{"config": {"model": "changed"}}', encoding="utf8"
)
assert opsmesh.get_plugin("consumer_a").module.get_settings().model == "changed"
assert opsmesh.get_plugin("consumer_b").module.get_settings().model == "consumer_b"
""",
    )


def test_global_config_without_declared_file(tmp_path):
    run_in_host(
        tmp_path,
        """
from pydantic import BaseModel
import opsmesh
class Settings(BaseModel):
    model: str
opsmesh.init(driver="~none")
assert opsmesh.get_plugin_config(Settings).model == "from-env"
""",
    )


@pytest.mark.parametrize("invalid", [False, True])
def test_non_adapter_provider_reads_each_consumers_config(tmp_path, invalid):
    provider = tmp_path / "search_provider.py"
    provider.write_text(
        """
from pydantic import BaseModel, Field
from opsmesh.plugin import PluginMetadata, get_plugin_dependency_configs

class Options(BaseModel):
    top_k: int = Field(gt=0)

__plugin_meta__ = PluginMetadata(
    name=__name__, description="", usage="", dependency_config=Options,
)

def read():
    return get_plugin_dependency_configs(Options)
""",
        encoding="utf8",
    )
    for name, top_k in [("consumer_a", 5), ("consumer_b", 0 if invalid else 20)]:
        plugin = tmp_path / name
        plugin.mkdir()
        (plugin / "__init__.py").write_text(
            """
from opsmesh.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name=__name__, description="", usage="", config_file="settings.json",
)
""",
            encoding="utf8",
        )
        (plugin / "settings.json").write_text(
            json.dumps({"dependencies": {"search_provider": {"top_k": top_k}}}),
            encoding="utf8",
        )
    run_in_host(
        tmp_path,
        f"""
from pathlib import Path
import opsmesh

opsmesh.init(driver="~none")
for name in ("search_provider", "consumer_a", "consumer_b"):
    plugin = opsmesh.load_plugin(name)
    assert plugin is not None, name
    plugin.manager.enable_plugin(plugin)
provider = opsmesh.require("search_provider")

started = []
def start():
    started.append(provider.read())
opsmesh.get_driver().run = start
if {invalid!r}:
    try:
        opsmesh.run()
    except ValueError as exc:
        assert "consumer_b.dependencies.search_provider" in str(exc), str(exc)
        assert "top_k" in str(exc)
    else:
        raise AssertionError("Invalid options reached the driver")
    assert not started
else:
    assert provider.read()["consumer_a"].top_k == 5
    opsmesh.run()
    assert len(started) == 1
    assert {{key: opts.top_k for key, opts in started[0].items()}} == {{
        "consumer_a": 5, "consumer_b": 20,
    }}
    started[0]["consumer_a"].top_k = 999
    assert provider.read()["consumer_a"].top_k == 5
    assert provider.read()["consumer_b"].top_k == 20
    Path("consumer_a/settings.json").write_text(
        '{{"dependencies": {{"search_provider": {{"top_k": 8}}}}}}',
        encoding="utf8",
    )
    assert provider.read()["consumer_a"].top_k == 8
    consumer_b = opsmesh.get_plugin("consumer_b")
    consumer_b.manager.disable_plugin(consumer_b)
    assert set(provider.read()) == {{"consumer_a"}}
assert not opsmesh.get_adapters()
""",
    )


@pytest.mark.parametrize(
    ("filename", "content", "error"),
    [
        ("settings.json", None, "missing"),
        ("../settings.json", "{}", "relative path"),
        ("settings.json", '{"config": {}, "config": {}}', "duplicate JSON key"),
        ("settings.json", '{"config": {"model": []}}', "consumer.config"),
        (
            "settings.json",
            '{"config": {"model": "ok"}, '
            '"dependencies": {"unused": {}}}',
            "consumer.dependencies.unused: provider is not loaded and enabled",
        ),
    ],
)
def test_invalid_file_stops_before_driver_run(
    tmp_path, filename, content, error
):
    package = tmp_path / "consumer"
    package.mkdir()
    if content is not None:
        (tmp_path / "settings.json").write_text(content, encoding="utf8")
        (package / "settings.json").write_text(content, encoding="utf8")
    (package / "__init__.py").write_text(
        f"""
from opsmesh.plugin import PluginMetadata
from pydantic import BaseModel
class Settings(BaseModel):
    model: str
__plugin_meta__ = PluginMetadata(
    name="Test", description="", usage="",
    config=Settings, config_file={filename!r},
)
""",
        encoding="utf8",
    )
    run_in_host(
        tmp_path,
        f"""
import opsmesh
opsmesh.init(driver="~none")
plugin = opsmesh.load_plugin("consumer")
assert plugin is not None
plugin.manager.enable_plugin(plugin)
def unexpected_run():
    raise AssertionError("Driver ran before validating invalid config")
opsmesh.get_driver().run = unexpected_run
try:
    opsmesh.run()
except ValueError as exc:
    assert {error!r} in str(exc), str(exc)
else:
    raise AssertionError("Expected invalid config to fail startup")
assert not opsmesh.get_adapters()
""",
    )
