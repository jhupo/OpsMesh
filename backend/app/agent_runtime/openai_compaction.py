from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from agents import OpenAIResponsesCompactionSession, Session
from openai import AsyncOpenAI

from backend.app.agent_runtime.contracts import AgentRunRequest
from backend.app.agent_runtime.openai_session import OpenAISessionAdapter
from backend.app.agent_runtime.token_estimation import estimate_token_upper_bound
from backend.app.model_providers.base_url import normalize_openai_compatible_base_url
from backend.app.model_providers.model_api import (
    OPENAI_CHAT_COMPLETIONS_API,
    canonical_model_api,
)
from backend.app.model_providers.provider_keys import canonical_model_provider


@asynccontextmanager
async def openai_run_session(
    request: AgentRunRequest,
) -> AsyncIterator[Session | None]:
    """Use the OpenAI SDK's native Responses compaction for persistent sessions."""

    session = OpenAISessionAdapter(request.session) if request.session is not None else None
    if session is None or not _supports_native_compaction(request):
        yield session
        return

    if request.api_key is None:
        raise ValueError("OpenAI responses compaction requires an explicit provider API key")
    async with AsyncOpenAI(
        api_key=request.api_key,
        base_url=normalize_openai_compatible_base_url(request.base_url),
    ) as client:
        compacting_session = OpenAIResponsesCompactionSession(
            session_id=session.session_id,
            underlying_session=session,
            client=client,
            model=request.model or request.agent_profile.model,
            should_trigger_compaction=_token_aware_compaction_policy(request),
        )
        yield compacting_session


def _supports_native_compaction(request: AgentRunRequest) -> bool:
    provider = canonical_model_provider(request.provider or "openai")
    model_api = canonical_model_api(request.model_api)
    return provider == "openai" and model_api != OPENAI_CHAT_COMPLETIONS_API


def _token_aware_compaction_policy(
    request: AgentRunRequest,
) -> Callable[[dict[str, object]], bool]:
    budget = request.context.metadata.get("context_budget")
    input_budget = budget.get("input_budget_tokens") if isinstance(budget, dict) else None
    threshold = max(1_024, int(input_budget * 0.75)) if isinstance(input_budget, int) else 24_576

    def should_compact(context: dict[str, object]) -> bool:
        items = context.get("session_items")
        if not isinstance(items, list):
            return False
        serialized = json.dumps(items, default=str, ensure_ascii=False, sort_keys=True)
        return estimate_token_upper_bound(serialized) >= threshold

    return should_compact
