"""Caller identity injection satisfies the native SDK's callback contract."""

import asyncio

import pytest
from agents import Agent, RunContextWrapper

from opsmesh.execution.runner import caller_instructions


@pytest.mark.parametrize("kind", ["text", "sync", "async", "none"])
def test_trusted_instructions_use_native_signature_and_preserve_each_agent(kind):
    async def scenario():
        first = Agent(name="First")
        second = Agent(name="Second")
        context = RunContextWrapper({"authorized": True})

        def dynamic(wrapper, agent):
            assert wrapper is context
            return agent.name + " original"

        async def asynchronous(wrapper, agent):
            return dynamic(wrapper, agent)

        original = {"text": "Static", "sync": dynamic, "async": asynchronous, "none": None}[kind]
        for agent in (first, second):
            agent.instructions = caller_instructions(original, '{"roles":["admin"]}')
        for agent in (first, second):
            prompt = await agent.get_system_prompt(context)
            assert prompt is not None and '"roles":["admin"]' in prompt
            if kind in {"sync", "async"}:
                assert prompt.startswith(agent.name + " original")
            elif kind == "text":
                assert prompt.startswith("Static")

    asyncio.run(scenario())
