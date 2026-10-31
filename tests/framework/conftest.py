"""Each test has isolated process registries, not a synthetic application stub."""

import sys
from collections.abc import Iterator

import pytest

from opsmesh.framework import init
from opsmesh.framework.matcher import matchers
from opsmesh.framework.message import _postprocessors, _preprocessors
from opsmesh.framework.plugin import manager


@pytest.fixture(autouse=True)
def framework_state() -> Iterator[None]:
    init()
    matchers.clear()
    manager.plugins.clear()
    _preprocessors.clear()
    _postprocessors.clear()
    yield
    for name in tuple(manager.plugins):
        for key in tuple(sys.modules):
            if key == name or key.startswith(name + "."):
                sys.modules.pop(key, None)
    manager.plugins.clear()
    matchers.clear()
    _preprocessors.clear()
    _postprocessors.clear()
