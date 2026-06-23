import json

import httpx

from backend.app.agent_runtime import anthropic_protocol
from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeEvent,
    AgentRuntimeToolResult,
)
from backend.app.agent_runtime.errors import normalize_agent_error
from backend.app.core.resilience import CircuitBreakerConfig, async_retry_with_circuit
from backend.app.security.redaction import redact_sensitive_payload

DEFAULT_MODEL_PROVIDER_CIRCUIT_CONFIG = CircuitBreakerConfig()


class AnthropicMessagesRunner:
    def __init__(
        self,
        *,
        max_attempts: int = 1,
        circuit_config: CircuitBreakerConfig = DEFAULT_MODEL_PROVIDER_CIRCUIT_CONFIG,
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._max_attempts = max(1, max_attempts)
        self._circuit_config = circuit_config
        self._timeout_seconds = timeout_seconds
        self._client = client

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        if not request.api_key:
            raise ValueError("Anthropic provider requires an api_key")
        result = await async_retry_with_circuit(
            key=anthropic_protocol.model_provider_circuit_key(request),
            func=lambda: self._run_once(request),
            max_attempts=self._max_attempts,
            circuit_config=self._circuit_config,
            should_retry=lambda exc: normalize_agent_error(exc).retryable,
        )
        return result

    async def _run_once(self, request: AgentRunRequest) -> AgentRunResult:
        user_input = self._input_for_request(request)
        messages = await self._messages_for_request(request, user_input)
        first_response = await self._create_message(request, messages)
        tool_uses = anthropic_protocol.tool_uses(first_response)
        if not tool_uses or request.tool_executor is None:
            result = anthropic_protocol.result_from_response(request, first_response)
            await self._persist_session_turn(request, user_input, result.final_output)
            return result

        tool_results = [
            (tool_use, self._execute_tool_use(request, tool_use))
            for tool_use in tool_uses
        ]
        messages.append(
            {
                "role": "assistant",
                "content": first_response.get("content", []),
            }
        )
        messages.append(
            {
                "role": "user",
                "content": [
                    self._tool_result_content(tool_use=tool_use, result=result)
                    for tool_use, result in tool_results
                ],
            }
        )
        final_response = await self._create_message(request, messages)
        events = [
            AgentRuntimeEvent(
                event_type="tool.completed",
                message="Anthropic tool call completed.",
                payload={
                    "tool_name": str(tool_use.get("name")),
                    "tool_call_id": str(tool_use.get("id")),
                    "status": result.status,
                    "metadata": redact_sensitive_payload(dict(result.metadata)),
                },
            )
            for tool_use, result in tool_results
        ]
        result = anthropic_protocol.result_from_response(request, final_response, events=events)
        await self._persist_session_turn(request, user_input, result.final_output)
        return result

    async def _messages_for_request(
        self,
        request: AgentRunRequest,
        user_input: str,
    ) -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        if request.session is not None:
            for item in await request.session.get_items():
                message = anthropic_protocol.session_item_to_message(item)
                if message is not None:
                    messages.append(message)
        messages.append({"role": "user", "content": user_input})
        return messages

    async def _persist_session_turn(
        self,
        request: AgentRunRequest,
        user_input: str,
        final_output: str,
    ) -> None:
        if request.session is None:
            return
        await request.session.add_items(
            [
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": final_output},
            ]
        )

    async def _create_message(
        self,
        request: AgentRunRequest,
        messages: list[dict[str, object]],
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": request.model or request.agent_profile.model,
            "max_tokens": anthropic_protocol.max_tokens(request.agent_profile.model_settings),
            "system": request.agent_profile.instructions,
            "messages": messages,
        }
        temperature = anthropic_protocol.float_setting(
            request.agent_profile.model_settings,
            "temperature",
        )
        if temperature is not None:
            payload["temperature"] = temperature
        tools = anthropic_protocol.tool_definitions(request)
        if tools:
            payload["tools"] = tools
            tool_choice = anthropic_protocol.tool_choice(request.agent_profile.model_settings)
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
        client = self._client
        if client is not None:
            response = await client.post(
                anthropic_protocol.messages_url(request.base_url),
                headers=anthropic_protocol.headers(request.api_key or ""),
                json=payload,
            )
            response.raise_for_status()
            return anthropic_protocol.json_object(response.json())
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as ephemeral_client:
            response = await ephemeral_client.post(
                anthropic_protocol.messages_url(request.base_url),
                headers=anthropic_protocol.headers(request.api_key or ""),
                json=payload,
            )
            response.raise_for_status()
            return anthropic_protocol.json_object(response.json())

    def _execute_tool_use(
        self,
        request: AgentRunRequest,
        tool_use: dict[str, object],
    ) -> AgentRuntimeToolResult:
        tool_name = str(tool_use.get("name") or "")
        if tool_name not in request.context.allowed_tools:
            return AgentRuntimeToolResult(
                status="failed",
                error={
                    "code": "tool_not_allowed",
                    "message": f"Tool {tool_name} is not present in runtime context.",
                },
            )
        if request.tool_executor is None:
            return AgentRuntimeToolResult(
                status="failed",
                error={"code": "tool_executor_missing", "message": "Tool executor missing"},
            )
        return request.tool_executor.execute_tool(
            context=request.context,
            tool_name=tool_name,
            arguments=anthropic_protocol.json_object(tool_use.get("input")),
        )

    def _tool_result_content(
        self,
        *,
        tool_use: dict[str, object],
        result: AgentRuntimeToolResult,
    ) -> dict[str, object]:
        return {
            "type": "tool_result",
            "tool_use_id": str(tool_use.get("id") or ""),
            "is_error": result.status != "completed",
            "content": json.dumps(
                result.output if result.status == "completed" else result.error,
                ensure_ascii=False,
                sort_keys=True,
            ),
        }

    def _input_for_request(self, request: AgentRunRequest) -> str:
        if not request.continuations:
            return request.input_text
        continuation_lines = [
            "- "
            + json.dumps(
                {
                    "tool_name": continuation.tool_name,
                    "status": continuation.status,
                    "result": continuation.result
                    if continuation.status == "completed"
                    else continuation.error,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            for continuation in request.continuations
        ]
        return (
            request.input_text
            + "\n\nCompleted runtime tool results:\n"
            + "\n".join(continuation_lines)
        )
