"""Native SDK smoke; this does not certify the OpsMesh product lifecycle."""

import asyncio
import os

import httpx2
import pytest
from agents import (
    Agent,
    ModelSettings,
    OpenAIProvider,
    RunConfig,
    Runner,
    SQLiteSession,
    function_tool,
)
from openai import AsyncOpenAI
from openai.types.shared import Reasoning

pytestmark = pytest.mark.openai_smoke


def test_real_responses_tool_and_private_history(tmp_path):
    required = (
        "OPSMESH_TEST_MODEL_API_KEY",
        "OPSMESH_TEST_MODEL_BASE_URL",
        "OPSMESH_TEST_MODEL_NAME",
    )
    if any(not os.environ.get(name) for name in required):
        pytest.skip("Real model credentials and endpoint are not configured")

    async def scenario():
        calls = []

        @function_tool
        def add(a: int, b: int) -> int:
            """Add the supplied integers."""
            calls.append((a, b))
            return a + b

        async with AsyncOpenAI(
            api_key=os.environ[required[0]],
            base_url=os.environ[required[1]],
            max_retries=0,
            timeout=90,
            http_client=httpx2.AsyncClient(
                trust_env=False,
                follow_redirects=False,
                transport=httpx2.AsyncHTTPTransport(retries=2, trust_env=False),
            ),
        ) as upstream:
            agent = Agent(
                name="CurrentNativeSmoke",
                instructions="Use add when asked. Reply with the numeric result only.",
                model=OpenAIProvider(openai_client=upstream).get_model(os.environ[required[2]]),
                model_settings=ModelSettings(
                    max_tokens=2048,
                    reasoning=Reasoning(
                        effort=os.environ.get("OPSMESH_TEST_MODEL_REASONING_EFFORT", "medium")
                    ),
                ),
                tools=[add],
            )
            path = str(tmp_path / "native.sqlite")
            history = SQLiteSession("private", path)
            restored = SQLiteSession("private", path)
            other = SQLiteSession("another-user", path)
            try:
                config = RunConfig(tracing_disabled=True, trace_include_sensitive_data=False)
                result = await Runner.run(
                    agent,
                    "Call add with a=19 and b=23.",
                    session=history,
                    max_turns=4,
                    run_config=config,
                )
                assert calls == [(19, 23)] and str(result.final_output).strip() == "42"
                assert len(await restored.get_items()) >= 4
                assert await other.get_items() == []
                following = await Runner.run(
                    agent,
                    "What was the numeric result? Do not call tools.",
                    session=restored,
                    max_turns=2,
                    run_config=config,
                )
                assert str(following.final_output).strip() == "42"
                assert calls == [(19, 23)]
                await restored.clear_session()
                assert await history.get_items() == []
            finally:
                for session in (history, restored, other):
                    session.close()

    asyncio.run(scenario())
