"""Delegate session compaction and its trigger policy to the locked Agents SDK."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from agents import OpenAIResponsesCompactionSession, Session
from openai import AsyncOpenAI

from backend.app.agents.execution.contracts import AgentRunRequest
from backend.app.agents.providers.model_api import OPENAI_CHAT_COMPLETIONS_API, canonical_model_api
from backend.app.agents.providers.policy import canonical_model_provider


@asynccontextmanager
async def openai_run_session(request: AgentRunRequest) -> AsyncIterator[Session | None]:
    session = cast(Session | None, request.session)
    if (
        session is None
        or canonical_model_provider(request.provider or "openai") != "openai"
        or canonical_model_api(request.model_api) == OPENAI_CHAT_COMPLETIONS_API
    ):
        yield session
        return
    if request.api_key is None:
        raise ValueError("OpenAI responses compaction requires an explicit provider API key")
    async with AsyncOpenAI(api_key=request.api_key, base_url=request.base_url) as client:
        yield OpenAIResponsesCompactionSession(
            session_id=session.session_id,
            underlying_session=session,
            client=client,
            model=request.model or request.agent_profile.model,
            compaction_mode="input",
        )
