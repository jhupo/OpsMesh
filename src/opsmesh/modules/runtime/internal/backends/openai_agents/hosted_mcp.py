"""Hosted MCP credentials stay runtime-local; approvals use SDK checkpoints or deployed policy."""

import inspect
import json
from typing import Any

from agents import HostedMCPTool
from agents.tool import MCPToolApprovalFunctionResult
from openai.types.responses.tool_param import Mcp
from pydantic import TypeAdapter

from opsmesh.framework.agents import AgentExecutionContext, HostedMCPBinding
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import (
    ApprovalPolicyDecision,
    HostedMCPApprovalRequest,
    RuntimeExtensions,
)


async def build_hosted_mcp(
    binding: HostedMCPBinding, context: AgentExecutionContext, extensions: RuntimeExtensions
) -> HostedMCPTool:
    config: dict[str, Any] = {
        "type": "mcp",
        "server_label": binding.server_label,
        "allowed_tools": list(binding.allowed_tools),
        "require_approval": binding.require_approval,
        "defer_loading": binding.defer_loading,
    }
    for name in ("server_url", "connector_id"):
        if value := getattr(binding, name):
            config[name] = value
    if binding.description:
        config["server_description"] = binding.description
    references = dict(binding.header_secret_refs)
    if references or binding.authorization_secret_ref:
        resolver = context.services.resolve_secret
        if resolver is None:
            raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "Hosted MCP credential resolver missing")

        async def secret(ref: str) -> str:
            value = await resolver(ref)
            if not isinstance(value, str) or not value:
                raise OpsMeshError("CREDENTIAL_UNAVAILABLE", "Hosted MCP credential unavailable")
            return value

        if binding.authorization_secret_ref:
            config["authorization"] = await secret(binding.authorization_secret_ref)
        if references:
            config["headers"] = {name: await secret(ref) for name, ref in references.items()}

    async def approve(request: Any) -> MCPToolApprovalFunctionResult:
        data = request.data
        decision = extensions.mcp_approval(binding.approval_policy_ref or "")(
            request.ctx_wrapper.context,
            HostedMCPApprovalRequest(
                server_label=data.server_label,
                tool_name=data.name,
                arguments=json.loads(data.arguments),
                request_id=data.id,
            ),
        )
        decision = await decision if inspect.isawaitable(decision) else decision
        if not isinstance(decision, ApprovalPolicyDecision):
            raise OpsMeshError(
                "EXTENSION_INVALID", "MCP approval policy must return ApprovalPolicyDecision"
            )
        result: MCPToolApprovalFunctionResult = {"approve": decision.approved}
        if decision.reason:
            result["reason"] = decision.reason
        return result

    return HostedMCPTool(
        tool_config=TypeAdapter(Mcp).validate_python(config),
        on_approval_request=approve if binding.approval_policy_ref else None,
    )
