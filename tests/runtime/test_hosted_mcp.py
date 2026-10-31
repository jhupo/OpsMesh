"""Hosted MCP authentication is local-only and SDK approval resumes by server identity."""

import json

import pytest
from openai.types.responses.response_output_item import McpApprovalRequest

from opsmesh.framework.agents import (
    AgentSpecification,
    HostedMCPBinding,
    ToolBinding,
    ToolSearchBinding,
)
from opsmesh.modules.runtime.api import ApprovalPolicyDecision
from opsmesh.modules.runtime.internal.backends.openai_agents.backend import OpenAIAgentsBackend
from opsmesh.modules.runtime.internal.extensions import RuntimeExtensionRegistry
from tests.runtime.test_openai_backend import ScriptedModel, context, message


def mcp_spec(policy=None):
    return AgentSpecification(
        id="a",
        name="A",
        hosted_tools=(
            HostedMCPBinding(
                server_label="docs",
                server_url="https://mcp.example.test/mcp",
                authorization_secret_ref="env:MCP_TOKEN",
                header_secret_refs={"X-Service-Key": "env:MCP_HEADER"},
                allowed_tools=("search",),
                approval_policy_ref=policy,
            ),
        ),
    )


def mcp_context():
    ctx = context()
    ctx.services["authorize_hosted_tool"] = lambda binding: None
    ctx.services["resolve_secret"] = lambda ref: "sensitive-runtime-token"
    return ctx


@pytest.mark.asyncio
async def test_hosted_mcp_approval_checkpoint_has_no_credentials():
    native = McpApprovalRequest(
        id="mcp1",
        type="mcp_approval_request",
        name="search",
        server_label="docs",
        arguments='{"q":"x"}',
    )
    model = ScriptedModel([[native], [message("done")]])
    registry = RuntimeExtensionRegistry()
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda *_: model)
    spec = mcp_spec()
    pending = await backend.run(spec, "hi", mcp_context())
    assert pending.pending[0].server_label == "docs"
    assert "sensitive-runtime-token" not in json.dumps(pending.checkpoint)
    native_tool = model.inputs[0][1]["tools"][0]
    assert native_tool.tool_config["authorization"] == "sensitive-runtime-token"
    result = await backend.run(
        spec,
        "ignored",
        mcp_context(),
        checkpoint=pending.checkpoint,
        decisions={pending.pending[0].id: False},
    )
    assert result.output == "done"
    inputs = model.inputs[-1][1]["input"]
    assert any(
        item.get("type") == "mcp_approval_response" and item["approve"] is False for item in inputs
    )


@pytest.mark.asyncio
async def test_hosted_mcp_deployed_policy_and_deferred_search_mapping():
    registry = RuntimeExtensionRegistry()
    seen = []
    registry.register_mcp_approval(
        "policy", lambda ctx, request: seen.append(request) or ApprovalPolicyDecision(approved=True)
    )
    native = McpApprovalRequest(
        id="mcp1", type="mcp_approval_request", name="search", server_label="docs", arguments="{}"
    )
    model = ScriptedModel([[native], [message("done")]])
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda *_: model)
    spec = mcp_spec("policy")
    spec = spec.model_copy(
        update={
            "hosted_tools": (*spec.hosted_tools, ToolSearchBinding()),
            "tools": (ToolBinding(name="lookup", defer_loading=True),),
        }
    )
    assert (await backend.run(spec, "hi", mcp_context())).output == "done"
    assert seen[0].server_label == "docs"
    tools = model.inputs[0][1]["tools"]
    assert next(item for item in tools if item.name == "lookup").defer_loading
    assert next(item for item in tools if item.name == "tool_search").execution == "server"
