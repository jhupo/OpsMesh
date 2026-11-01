"""Resolve platform model profiles without exposing credentials to the control plane."""

import dataclasses
from typing import Any

from agents import ModelSettings, OpenAIChatCompletionsModel, OpenAIResponsesModel
from agents.model_settings import MCPToolChoice
from agents.models.openai_provider import OpenAIProvider
from openai import AsyncOpenAI
from pydantic import TypeAdapter

from opsmesh.framework.agents import (
    AgentExecutionContext,
    AgentSpecification,
    HostedMCPToolSelection,
    merge_model_settings,
)
from opsmesh.framework.errors import OpsMeshError

from .execution_state import execution_state
from .model_validation import validate_model_configuration


def resolve_settings(spec: AgentSpecification, profile: dict[str, Any] | None) -> ModelSettings:
    settings = merge_model_settings((profile or {}).get("settings", {}), spec.model_settings)
    if spec.tool_choice is not None:
        settings["tool_choice"] = (
            MCPToolChoice(spec.tool_choice.server_label, spec.tool_choice.name)
            if isinstance(spec.tool_choice, HostedMCPToolSelection)
            else spec.tool_choice
        )
    if set(settings) - {field.name for field in dataclasses.fields(ModelSettings)}:
        raise OpsMeshError("CONFIG_INVALID", "Unknown model setting")
    return TypeAdapter(ModelSettings).validate_python(settings)


async def resolve_profile_model(
    spec: AgentSpecification, context: AgentExecutionContext, clients: list[AsyncOpenAI]
) -> tuple[Any, dict[str, Any] | None]:
    profiles = execution_state(context).model_profiles
    profile = profiles.get(spec.ref)
    if profile is None and spec.model_profile_ref:
        prepare = context.services.prepare_model_profile
        if prepare is not None:
            profile = await prepare(spec)
        else:
            profile = execution_state(context).model_profile
        if profile is None:
            raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "Published model profile is unavailable")
    if profile is None:
        client = AsyncOpenAI(max_retries=0)
        clients.append(client)
        return OpenAIProvider(
            openai_client=client,
            strict_feature_validation=True,
            use_responses_websocket=spec.model_transport == "websocket",
        ).get_model(spec.model), None
    profile = dict(profile)
    if spec.model_profile_ref != f"{profile.get('id')}@{profile.get('revision')}":
        raise OpsMeshError("CONFIG_INVALID", "Model profile revision does not match the agent")
    validate_model_configuration(spec, profile)
    secret_ref = profile.get("secret_ref")
    secret = None
    if secret_ref:
        resolver = context.services.resolve_secret
        if resolver is None:
            raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "Model credential resolver missing")
        secret = await resolver(secret_ref)
    client = AsyncOpenAI(api_key=secret, base_url=profile.get("base_url"), max_retries=0)
    clients.append(client)
    if profile.get("api", "responses") == "responses":
        if spec.model_transport == "websocket":
            return OpenAIProvider(
                openai_client=client, use_responses=True, use_responses_websocket=True
            ).get_model(profile["model"]), profile
        return OpenAIResponsesModel(profile["model"], client), profile
    if profile["api"] == "chat_completions":
        if spec.model_transport == "websocket" or spec.session_compaction:
            raise OpsMeshError(
                "FEATURE_UNSUPPORTED", "WebSocket and compaction require Responses API"
            )
        return OpenAIChatCompletionsModel(
            profile["model"], client, strict_feature_validation=True
        ), profile
    raise OpsMeshError("FEATURE_UNSUPPORTED", "Model API is unsupported")
