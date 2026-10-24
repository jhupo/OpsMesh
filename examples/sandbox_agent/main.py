from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import PurePosixPath
from typing import Any

import docker
from agents import RunConfig, RunContextWrapper, Runner, RunState
from agents.sandbox import Manifest, SandboxAgent
from agents.sandbox.entries import File
from agents.sandbox.sandboxes.docker import DockerSandboxClient, DockerSandboxClientOptions
from opsmesh_sdk import InvocationContext, PluginContext, Suspended
from opsmesh_sdk.sessions import SandboxContext

from .schemas import SandboxConfiguration


@asynccontextmanager
async def docker_sandbox(context: SandboxContext) -> AsyncIterator[DockerSandboxClient]:
    connection = docker.from_env()
    try:
        yield DockerSandboxClient(connection)
    finally:
        connection.close()


async def invoke(context: InvocationContext, value: Any) -> Any:
    config = SandboxConfiguration.model_validate(context.config)
    model = await context.resources.model(
        config.connection_id, config.model, reserve_tokens=config.reserve_tokens
    )
    files = {}
    for name, content in config.initial_files.items():
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] == ".opsmesh"
        ):
            raise ValueError("Initial files must be relative application files")
        files[name] = File(content=content.encode())
    manifest = Manifest(root=context.session.sandbox_root, entries=files)
    async with docker_sandbox(
        SandboxContext(context.user, dict(context.config), context.session.root_dir)
    ) as client:
        options = DockerSandboxClientOptions(image=config.image, network_mode="none")
        async with context.session.sandbox(client, options, manifest=manifest) as sandbox:
            agent = SandboxAgent[InvocationContext](
                name="Sandbox assistant", model=model, instructions=config.instructions
            )
            run_input: Any = value
            if context.checkpoint is not None:
                state = await RunState.from_json(
                    agent,
                    context.checkpoint["run_state"],
                    context_override=RunContextWrapper(context),
                    strict_context=True,
                )
                if state.get_interruptions():
                    if context.decision is None or type(context.decision.value) is not bool:
                        raise ValueError("A boolean approval response is required")
                    for interruption in state.get_interruptions():
                        if context.decision.value:
                            state.approve(interruption)
                        else:
                            state.reject(interruption)
                run_input = state
            result = await Runner.run(
                agent,
                run_input,
                context=context,
                session=context.session.history,
                run_config=RunConfig(sandbox=sandbox, trace_include_sensitive_data=False),
                max_turns=32,
            )
            if result.interruptions:
                return Suspended(
                    checkpoint={
                        "run_state": result.to_state().to_json(
                            context_serializer=lambda _: {}, strict_context=True
                        )
                    },
                    prompt="Approve the pending sandbox tool calls?",
                    details={
                        "calls": [
                            item.raw_item
                            if isinstance(item.raw_item, dict)
                            else item.raw_item.model_dump(mode="json")
                            for item in result.interruptions
                        ]
                    },
                )
            return result.final_output


def setup(context: PluginContext) -> None:
    context.entry("chat", invoke, resume=invoke)
    context.sandbox("docker", docker_sandbox)
