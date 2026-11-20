"""Verify the current file-backed consumer plugin example."""

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_file_backed_consumer_example_loads_without_channels():
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(ROOT / "src"), str(ROOT / "examples" / "plugin-consumer")]
        ),
    }
    script = """
import opsmesh
opsmesh.init(driver="~none")
plugin = opsmesh.load_plugin("example_consumer")
assert plugin is not None
plugin.manager.enable_plugin(plugin)
opsmesh.get_driver().run = lambda: None
opsmesh.run()
assert plugin.module.get_settings().model == "example-model"
assert plugin.module.get_settings().greeting == "hello"
assert not opsmesh.get_adapters()
assert "opsmesh.plugins.channels" not in __import__("sys").modules
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
