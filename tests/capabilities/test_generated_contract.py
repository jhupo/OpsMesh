"""The shared five-type capability API keeps its discriminator in generated SDK DTOs."""

import sys
from types import ModuleType

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from opsmesh.capabilities.routes import router
from scripts.generate_sdk_api import collect_operations, load_config, render_group_module


@pytest.mark.parametrize(
    ("kind", "configuration"),
    [
        ("mcp", {"transport": "remote", "endpoint": "https://example.com/mcp"}),
        (
            "tool",
            {"execution_mode": "sdk_export", "entrypoint": "a:b", "operations": [{"name": "run"}]},
        ),
        ("skill", None),
        ("extension", {"operations": []}),
        (
            "channel",
            {
                "protocol": "example",
                "operations": [
                    {"name": "receive", "entrypoint": "a:b"},
                    {"name": "send", "entrypoint": "a:c"},
                ],
            },
        ),
    ],
)
def test_generated_definition_request_preserves_declaration_type(monkeypatch, kind, configuration):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    document = app.openapi()
    config = load_config()
    source = render_group_module(
        "capabilities", collect_operations(document, config), document, config
    )
    module = ModuleType("generated_capability_contract")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(source, "<generated-capabilities>", "exec"), module.__dict__)
    request = module.DefinitionInput.model_validate(
        {
            "key": "sample",
            "name": "Sample",
            "declaration": {"type": kind, "configuration": configuration},
        }
    )
    assert request.declaration.type == kind
    assert request.model_dump(mode="json")["declaration"]["type"] == kind
    with pytest.raises(ValidationError):
        module.DefinitionInput.model_validate(
            {
                "key": "sample",
                "name": "Sample",
                "declaration": {"type": "unknown"},
            }
        )
