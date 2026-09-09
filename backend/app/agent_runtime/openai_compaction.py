from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from agents import OpenAIResponsesCompactionSession, Session
from openai import AsyncOpenAI

from backend.app.agent_runtime.contracts import AgentRunRequest, AgentRuntimeSession
from backend.app.model_providers.base_url import normalize_openai_compatible_base_url
from backend.app.model_providers.model_api import (
    OPENAI_CHAT_COMPLETIONS_API,
    canonical_model_api,
)
from backend.app.model_providers.provider_keys import canonical_model_provider


@asynccontextmanager
async def openai_run_session(
    request: AgentRunRequest,
) -> AsyncIterator[AgentRuntimeSession | None]:
    """Use the OpenAI SDK's native Responses compaction for persistent sessions."""

    session = request.session
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
            underlying_session=cast(Session, session),
            client=client,
            model=request.model or request.agent_profile.model,
        )
        yield cast(AgentRuntimeSession, compacting_session)


def _supports_native_compaction(request: AgentRunRequest) -> bool:
    provider = canonical_model_provider(request.provider or "openai")
    model_api = canonical_model_api(request.model_api)
    return provider == "openai" and model_api != OPENAI_CHAT_COMPLETIONS_API
