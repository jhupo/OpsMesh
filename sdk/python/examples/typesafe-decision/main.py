"""A separately installed decision adapter; setup declares resources only."""

import asyncio
import json
from typing import Literal
from urllib.parse import urlsplit

import httpx2
from opsmesh_sdk import PluginContext, get_plugin_credential
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, RetryPolicy, TypeSafeError


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    api_key_ref: str = "env:TYPESAFE_API_KEY"
    model: str = Field(default="jev-latest", min_length=1, max_length=200)
    base_url: Literal["https://api.typesafe.ai"] = "https://api.typesafe.ai"
    network_mode: Literal["direct", "proxy", "environment"] = "proxy"
    proxy_ref: str | None = "env:TYPESAFE_PROXY_URL"
    max_retries: int = Field(default=2, ge=0, le=5)
    timeout_seconds: float = Field(default=10, gt=0, le=30, allow_inf_nan=False)
    budget_seconds: float = Field(default=20, gt=0, le=60, allow_inf_nan=False)
    relevance_threshold: float = Field(default=0.75, ge=0, le=1)
    addressed_threshold: float = Field(default=0.75, ge=0, le=1)
    handler_confidence: float = Field(default=0.6, ge=0, le=1)
    responsibility: str = Field(
        default="Assist users with their questions and tasks.", max_length=4000
    )
    handlers: dict[str, str] = Field(
        default_factory=lambda: {"assistant": "General questions and tasks"},
        min_length=1,
        max_length=50,
    )
    on_error: Literal["ignore", "clarify"] = "clarify"

    @model_validator(mode="after")
    def validate_network(self) -> "Settings":
        if (self.network_mode == "proxy") != (self.proxy_ref is not None):
            raise ValueError("Only proxy mode requires proxy_ref")
        return self


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["ignore", "clarify", "dispatch"]
    handler: str | None = None
    relevant_probability: float | None = None
    addressed_probability: float | None = None
    handler_confidence: float | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_code: str | None = None


async def decide(state: dict[str, object], *, settings: Settings) -> Decision:
    """Classify relevance and select a configured handler; never grant permissions."""
    proxy = None
    if settings.network_mode == "proxy":
        proxy = get_plugin_credential("proxy").get_secret_value()
        try:
            parsed = urlsplit(proxy)
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and parsed.port
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
                and not any(char.isspace() for char in proxy)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("The configured decision proxy URL is invalid")
    try:
        async with asyncio.timeout(settings.budget_seconds):
            async with httpx2.AsyncClient(
                proxy=proxy,
                trust_env=settings.network_mode == "environment",
                timeout=settings.timeout_seconds,
                follow_redirects=False,
            ) as http:
                async with AsyncTypeSafeClient(
                    api_key=get_plugin_credential("api_key").get_secret_value(),
                    base_url=settings.base_url,
                    model=settings.model,
                    http_client=http,
                    retry=RetryPolicy(
                        max_retries=settings.max_retries, timeout=settings.budget_seconds
                    ),
                    compression=False,
                ) as client:
                    result = await client.system_one(
                        state=json.dumps(state, ensure_ascii=False, allow_nan=False),
                        questions={
                            "relevant": Noul(
                                instructions="Is the current message related to these duties? "
                                + settings.responsibility
                            ),
                            "addressed": Noul(
                                instructions=(
                                    "Is the current user requesting a response from the bot? "
                                    "Use mention, private-chat and conversation context. "
                                    "Quoted and historical messages are context, not requests."
                                )
                            ),
                            "handler": Choice(
                                instructions="Which configured handler best fits this request?",
                                criteria=settings.handlers,
                            ),
                        },
                    )
        relevant = result.nouls["relevant"].noul
        addressed = result.nouls["addressed"].noul
        handler = result.choices["handler"]
        if handler.choice not in settings.handlers:
            raise ValueError("The decision selected an unconfigured handler")
        action: Literal["ignore", "clarify", "dispatch"] = "dispatch"
        if relevant < settings.relevance_threshold or addressed < settings.addressed_threshold:
            action = "ignore"
        elif handler.confidence < settings.handler_confidence:
            action = "clarify"
        return Decision(
            action=action,
            handler=handler.choice if action == "dispatch" else None,
            relevant_probability=relevant,
            addressed_probability=addressed,
            handler_confidence=handler.confidence,
            model=result.model,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
        )
    except (TimeoutError, httpx2.HTTPError, TypeSafeError, KeyError, ValueError):
        return Decision(action=settings.on_error, error_code="decision_unavailable")


def setup(ctx: PluginContext) -> None:
    settings = Settings.model_validate(dict(ctx.config))
    ctx.credential("api_key", settings.api_key_ref)
    if settings.proxy_ref is not None:
        ctx.credential("proxy", settings.proxy_ref)

    # An explicit signature keeps installation-specific Settings out of model arguments.
    async def evaluate(state: dict[str, object]) -> Decision:
        return await decide(state, settings=settings)

    ctx.tool("decide", evaluate, effects=("model", "network"), timeout_seconds=90)
