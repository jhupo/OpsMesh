"""Text Runner completion of pre-approval, resource selectors and versioned prompt bindings."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from opsmesh.framework.agents import AgentSpecification, GuardrailBinding, ToolBinding
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.capabilities.api import PreparedAgentResources
from opsmesh.modules.runtime.api import GuardrailDecision, PromptTemplateDefinition
from opsmesh.modules.runtime.internal.backends.openai_agents.backend import OpenAIAgentsBackend
from opsmesh.modules.runtime.internal.backends.openai_agents.execution_state import execution_state
from opsmesh.modules.runtime.internal.backends.openai_agents.models import resolve_profile_model
from opsmesh.modules.runtime.internal.extensions import RuntimeExtensionRegistry
from tests.runtime.test_openai_backend import ScriptedModel, call, context, message


@pytest.mark.asyncio
async def test_input_guardrail_runs_before_approval_and_again_after_approval():
    checked = []
    registry = RuntimeExtensionRegistry()
    registry.register_guardrail(
        "check", lambda ctx, args: checked.append(args) or GuardrailDecision(allowed=True)
    )
    spec = AgentSpecification(
        id="a",
        name="A",
        pre_approval_tool_input_guardrails=True,
        tools=(ToolBinding(name="write", requires_approval=True),),
        guardrails=(GuardrailBinding(ref="check", stage="tool_input", tool_name="write"),),
    )
    model = ScriptedModel([[call("write")], [message("done")]])
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda *_: model)
    paused = await backend.run(spec, "go", context())
    assert len(checked) == 1 and len(paused.pending) == 1
    await backend.run(
        spec,
        "ignored",
        context(),
        checkpoint=paused.checkpoint,
        decisions={paused.pending[0].id: True},
    )
    assert len(checked) == 2


@pytest.mark.asyncio
async def test_denied_preapproval_never_creates_pending_approval():
    registry = RuntimeExtensionRegistry()
    registry.register_guardrail("deny", lambda ctx, args: GuardrailDecision(allowed=False))
    spec = AgentSpecification(
        id="a",
        name="A",
        pre_approval_tool_input_guardrails=True,
        tools=(ToolBinding(name="write", requires_approval=True),),
        guardrails=(GuardrailBinding(ref="deny", stage="tool_input"),),
    )
    model = ScriptedModel([[call("write")]])
    with pytest.raises(OpsMeshError) as error:
        await OpenAIAgentsBackend(registry, model_resolver=lambda *_: model).run(
            spec, "go", context()
        )
    assert error.value.code == "GUARDRAIL_BLOCKED"


@pytest.mark.asyncio
async def test_sdk_function_concurrency_limit_bounds_actual_invocations():
    current = peak = 0

    async def invoke(*args):
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        await asyncio.sleep(0.01)
        current -= 1
        return "ok"

    ctx = context()
    ctx.invoke_tool = invoke
    spec = AgentSpecification(
        id="a", name="A", max_function_tool_concurrency=1, tools=(ToolBinding(name="work"),)
    )
    model = ScriptedModel(
        [[call("work", call_id="1"), call("work", call_id="2")], [message("done")]]
    )
    await OpenAIAgentsBackend(RuntimeExtensionRegistry(), model_resolver=lambda *_: model).run(
        spec, "go", ctx
    )
    assert peak == 1


@pytest.mark.asyncio
async def test_dynamic_mcp_selector_and_stop_alias_resolve_to_authorized_name():
    binding = ToolBinding(
        name="mcp_docs_find_abc", tool_ref="gateway@1", aliases=("mcp:docs@1/find",)
    )

    async def prepare(spec):
        return PreparedAgentResources(tools=(binding,))

    ctx = context()
    ctx.services.prepare_agent_resources = prepare
    spec = AgentSpecification(
        id="a",
        name="A",
        mcp_refs=("docs@1",),
        tool_choice="mcp:docs@1/find",
        tool_completion="stop_at_tools",
        stop_tools=("mcp:docs@1/find",),
    )
    model = ScriptedModel([[call(binding.name)]])
    outcome = await OpenAIAgentsBackend(
        RuntimeExtensionRegistry(), model_resolver=lambda *_: model
    ).run(spec, "go", ctx)
    assert outcome.output and len(model.inputs) == 1
    assert model.inputs[0][1]["model_settings"].tool_choice == binding.name
    missing = spec.model_copy(update={"tool_choice": "mcp:docs@1/not-authorized"})
    with pytest.raises(OpsMeshError) as error:
        await OpenAIAgentsBackend(RuntimeExtensionRegistry(), model_resolver=lambda *_: model).run(
            missing, "go", ctx
        )
    assert error.value.code == "TOOL_SELECTION_UNAVAILABLE" and len(model.inputs) == 1


@pytest.mark.asyncio
async def test_versioned_prompt_registration_defaults_and_checkpoint_identity():
    template = PromptTemplateDefinition(
        id="answer",
        remote_id="pmpt_test",
        remote_version="7",
        defaults={"tone": "plain"},
        required_variables=("topic",),
        allowed_variables=("tone", "topic"),
    )
    registry = RuntimeExtensionRegistry()
    registry.register_prompt_template(template)
    template.defaults["tone"] = "mutated externally"
    spec = AgentSpecification(
        id="a",
        name="A",
        prompt_template_ref="answer@1",
        prompt_variables={"topic": "tests"},
        tools=(ToolBinding(name="write", requires_approval=True),),
    )
    model = ScriptedModel([[call("write")]])
    paused = await OpenAIAgentsBackend(registry, model_resolver=lambda *_: model).run(
        spec, "go", context()
    )
    assert model.inputs[0][1]["prompt"] == {
        "id": "pmpt_test",
        "version": "7",
        "variables": {"tone": "plain", "topic": "tests"},
    }
    changed = RuntimeExtensionRegistry()
    changed.register_prompt_template(template.model_copy(update={"remote_version": "8"}))
    with pytest.raises(OpsMeshError) as error:
        await OpenAIAgentsBackend(changed, model_resolver=lambda *_: model).run(
            spec, "ignored", context(), checkpoint=paused.checkpoint
        )
    assert error.value.code == "CHECKPOINT_INCOMPATIBLE"
    with pytest.raises(OpsMeshError):
        registry.register_prompt_template(template)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings,extra",
    [
        ({"context_management": [{"type": "compaction", "compact_threshold": 1000}]}, {}),
        ({"response_include": ["message.output_text.logprobs"]}, {}),
        ({"reasoning": {"summary": "auto"}}, {}),
        ({}, {"prompt": {"id": "pmpt_test"}}),
    ],
)
async def test_chat_api_rejects_unsupported_configuration_before_secret_resolution(settings, extra):
    ctx = context()
    accessed = []
    ctx.services.resolve_secret = AsyncMock(wraps=lambda ref: accessed.append(ref))
    execution_state(ctx).model_profile = {
        "id": "chat",
        "revision": 1,
        "api": "chat_completions",
        "model": "offline",
        "secret_ref": "env:secret",
    }
    spec = AgentSpecification(
        id="a", name="A", model_profile_ref="chat@1", model_settings=settings, **extra
    )
    with pytest.raises(OpsMeshError) as error:
        await resolve_profile_model(spec, ctx, [])
    assert error.value.code == "FEATURE_UNSUPPORTED" and accessed == []


@pytest.mark.asyncio
async def test_model_declared_setting_policy_prevents_unsupported_temperature():
    ctx = context()
    execution_state(ctx).model_profile = {
        "id": "reasoner",
        "revision": 1,
        "api": "responses",
        "model": "offline",
        "unsupported_settings": ["temperature"],
    }
    with pytest.raises(OpsMeshError) as error:
        await resolve_profile_model(
            AgentSpecification(
                id="a", name="A", model_profile_ref="reasoner@1", model_settings={"temperature": 1}
            ),
            ctx,
            [],
        )
    assert "setting:temperature" in error.value.details["features"]


@pytest.mark.asyncio
async def test_mcp_config_strict_names_dynamic_filter_and_safe_error_formatter():
    from opsmesh.framework.agents import MCPToolConfiguration

    registry = RuntimeExtensionRegistry()
    seen = []
    registry.register_visibility(
        "visible", lambda ctx, data: seen.append(data) or data["source_name"] != "hidden"
    )
    registry.register_mcp_failure_formatter(
        "failure", lambda ctx, failure: f"retry {failure.tool_name}: {failure.error.code}"
    )
    bindings = tuple(
        ToolBinding(
            name=f"mcp_docs_{name}_hash",
            aliases=(f"mcp:docs@1/{name}",),
            resource_ref="docs@1",
            source_name=name,
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        )
        for name in ("find", "hidden")
    )

    async def prepare(spec):
        return PreparedAgentResources(tools=bindings)

    async def fail(*args):
        raise OpsMeshError(
            "TOOL_EXECUTION_FAILED",
            "private endpoint and credential must not enter model",
            details={"secret": "hidden"},
        )

    ctx = context()
    ctx.services.prepare_agent_resources = prepare
    ctx.invoke_tool = fail
    spec = AgentSpecification(
        id="a",
        name="A",
        mcp_refs=("docs@1",),
        mcp_config=MCPToolConfiguration(
            convert_schemas_to_strict=True,
            include_server_in_tool_names=False,
            tool_filter_ref="visible",
            failure_behavior="message",
            failure_formatter_ref="failure",
        ),
    )
    model = ScriptedModel([[call("find", '{"query":"x"}')], [message("handled")]])
    outcome = await OpenAIAgentsBackend(registry, model_resolver=lambda *_: model).run(
        spec, "go", ctx
    )
    assert outcome.output == "handled"
    tool = model.inputs[0][1]["tools"][0]
    assert [tool.name for tool in model.inputs[0][1]["tools"]] == ["find"]
    assert tool.strict_json_schema and tool.params_json_schema["required"] == ["query"]
    assert tool.params_json_schema["additionalProperties"] is False
    assert seen and all(item["server_ref"] == "docs@1" for item in seen)
    second_input = str(model.inputs[1][1]["input"])
    assert "retry find: TOOL_EXECUTION_FAILED" in second_input
    assert "private endpoint" not in second_input


@pytest.mark.asyncio
async def test_mcp_error_message_policy_cannot_swallow_authorization_denial():
    from opsmesh.framework.agents import MCPToolConfiguration

    binding = ToolBinding(name="mcp_find", resource_ref="docs@1", source_name="find")

    async def prepare(spec):
        return PreparedAgentResources(tools=(binding,))

    async def deny(*args):
        raise OpsMeshError("AUTHORIZATION_DENIED", "Permission revoked")

    ctx = context()
    ctx.services.prepare_agent_resources = prepare
    ctx.invoke_tool = deny
    spec = AgentSpecification(
        id="a",
        name="A",
        mcp_refs=("docs@1",),
        mcp_config=MCPToolConfiguration(failure_behavior="message"),
    )
    model = ScriptedModel([[call("mcp_find")]])
    with pytest.raises(OpsMeshError) as error:
        await OpenAIAgentsBackend(RuntimeExtensionRegistry(), model_resolver=lambda *_: model).run(
            spec, "go", ctx
        )
    assert error.value.code == "AUTHORIZATION_DENIED", repr(error.value.__cause__)


@pytest.mark.asyncio
async def test_hosted_mcp_tool_choice_names_an_explicitly_allowed_server_tool():
    from agents.models.openai_responses import Converter
    from pydantic import ValidationError

    from opsmesh.framework.agents import HostedMCPBinding, HostedMCPToolSelection

    server = HostedMCPBinding(
        server_label="docs", server_url="https://example.test/mcp", allowed_tools=("find",)
    )
    spec = AgentSpecification(
        id="a",
        name="A",
        hosted_tools=(server,),
        tool_choice=HostedMCPToolSelection(server_label="docs", name="find"),
    )
    model = ScriptedModel([[message("done")]])
    ctx = context()
    ctx.services.authorize_hosted_tool = AsyncMock(wraps=lambda binding: None)
    await OpenAIAgentsBackend(RuntimeExtensionRegistry(), model_resolver=lambda *_: model).run(
        spec, "go", ctx
    )
    actual = model.inputs[0][1]["model_settings"].tool_choice
    assert Converter.convert_tool_choice(actual) == {
        "type": "mcp",
        "server_label": "docs",
        "name": "find",
    }
    with pytest.raises(ValidationError, match="allowed server tool"):
        AgentSpecification(
            id="a",
            name="A",
            hosted_tools=(server,),
            tool_choice=HostedMCPToolSelection(server_label="docs", name="delete"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["run", "agent"])
async def test_hooks_expose_call_metadata_and_explicit_detached_content(scope):
    from opsmesh.framework.agents import HookBinding

    registry = RuntimeExtensionRegistry()
    metadata, content = [], []
    registry.register_hook("metadata", lambda ctx, stage, data: metadata.append((stage, data)))
    registry.register_hook("content", lambda ctx, stage, data: content.append((stage, data)))
    spec = AgentSpecification(
        id="a",
        name="A",
        instructions="private instructions",
        tools=(ToolBinding(name="read"),),
        hooks=(
            HookBinding(ref="metadata", scope=scope),
            HookBinding(ref="content", scope=scope, include_payload=True),
        ),
    )
    model = ScriptedModel([[call("read")], [message("finished")]])
    await OpenAIAgentsBackend(registry, model_resolver=lambda *_: model).run(
        spec, "private input", context()
    )
    plain = dict(metadata)
    rich = dict(content)
    assert "private" not in str(plain)
    assert "output" not in plain["agent_end"] and "result" not in plain["tool_end"]
    assert plain["tool_start"]["call_id"] == plain["tool_end"]["call_id"] == "call1"
    assert rich["model_start"]["system_prompt"] == "private instructions"
    assert isinstance(rich["model_end"]["output_items"][0], dict)
    assert rich["agent_end"]["output"] == "finished"
    assert "input_tokens" in plain["model_end"]["usage"]
