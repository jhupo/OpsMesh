"""Worker-side process/RPC supervision; the actual provider SDK runs inside Runtime."""

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from functools import partial

from backend.app.agents.execution.contracts import AgentRunRequest, AgentRunResult
from backend.app.agents.execution.errors import (
    AgentRuntimeCancelledError,
    AgentRuntimePolicyError,
    AgentRuntimeProviderError,
)
from backend.app.runtime.agent_host.broker import RuntimeCallbackBroker
from backend.app.runtime.agent_host.wire import RESULT, STREAM_EVENT, RpcFrame, RuntimeRunInput
from backend.app.runtime.backends.docker import DockerSandboxSessionExecutor
from backend.app.runtime.instances.contracts import DockerRuntimeClient
from backend.app.shared.concurrency import BlockingIO


@dataclass(frozen=True)
class RuntimeAgentExecutor:
    docker_client: DockerRuntimeClient
    io: BlockingIO

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        if request.sandbox is None or not isinstance(
            request.sandbox.session.executor, DockerSandboxSessionExecutor
        ):
            raise AgentRuntimePolicyError(
                code="sdk_runtime_host_unsupported",
                message="SDK host requires a Docker Runtime",
                event_type="runtime.authorization_blocked",
                metadata={},
            )
        wire = RuntimeRunInput.from_request(request)
        runtime = request.sandbox.session.executor
        channel = await self.io.run(
            partial(
                self.docker_client.open_agent_channel,
                runtime.container_id,
                working_dir=wire.manifest.root,
            )
        )
        callbacks: dict[str, asyncio.Task[None]] = {}
        pid: int | None = None
        broker = RuntimeCallbackBroker(request)

        async def reply(frame: RpcFrame) -> None:
            try:
                payload = await broker.dispatch(frame.method or "", frame.payload)
                response = RpcFrame(type="reply", id=frame.id, payload=payload)
            except AgentRuntimeCancelledError:
                response = RpcFrame(type="reply", id=frame.id, error={"code": "run_cancelled"})
            except Exception as error:
                # Do not serialize callback arguments, credentials or database errors.
                response = RpcFrame(type="reply", id=frame.id, error={"code": type(error).__name__})
            await channel.send(response.encoded())

        try:
            await channel.attach()
            await channel.send(
                RpcFrame(type="start", payload=wire.model_dump(mode="json")).encoded()
            )
            while True:
                frame = RpcFrame.model_validate_json(await channel.receive())
                if frame.type == "ready":
                    payload = frame.payload
                    if not isinstance(payload, dict) or payload.get("run_id") != str(
                        request.context.run_id
                    ):
                        raise ValueError("SDK host returned a different run identity")
                    process_id = payload.get("pid")
                    if not isinstance(process_id, int) or process_id <= 1 or pid is not None:
                        raise ValueError("SDK host returned an invalid process identity")
                    pid = process_id
                elif frame.type == "event":
                    if request.event_sink:
                        request.event_sink(STREAM_EVENT.validate_python(frame.payload))
                elif frame.type == "log":
                    payload = frame.payload
                    if (
                        not isinstance(payload, dict)
                        or payload.get("logger") != "openai-agents.openai.compaction"
                    ):
                        raise ValueError("Unexpected SDK logger")
                    level, message = payload.get("level"), payload.get("message")
                    if level not in {
                        logging.DEBUG,
                        logging.INFO,
                        logging.WARNING,
                        logging.ERROR,
                    } or not isinstance(message, str):
                        raise ValueError("Invalid SDK log frame")
                    logging.getLogger("openai-agents.openai.compaction").log(
                        level, "%s", message[:8192]
                    )
                elif frame.type == "call":
                    if not frame.id or frame.id in callbacks or pid is None:
                        raise ValueError("Invalid SDK callback identity")
                    callbacks = {key: task for key, task in callbacks.items() if not task.done()}
                    if len(callbacks) >= 64:
                        raise ValueError("SDK host exceeded concurrent callback capacity")
                    callbacks[frame.id] = asyncio.create_task(
                        reply(frame), name=f"sdk-rpc:{frame.method}"
                    )
                elif frame.type == "result":
                    await asyncio.gather(*callbacks.values())
                    return RESULT.validate_python(frame.payload)
                elif frame.type == "error":
                    _raise_host_error(frame.error or {})
                else:
                    raise ValueError("Unexpected SDK host frame")
        finally:
            # Kill the host process group before draining tool callbacks. Closing an
            # await or Docker socket alone does not terminate a remote subprocess.
            try:
                if pid is not None:
                    await self.io.run(
                        partial(
                            self.docker_client.terminate_agent_process, runtime.container_id, pid
                        )
                    )
            finally:
                for task in callbacks.values():
                    task.cancel()
                await asyncio.gather(*callbacks.values(), return_exceptions=True)
                with suppress(ConnectionError, BrokenPipeError):
                    await channel.close()


def _raise_host_error(error: dict[str, object]) -> None:
    kind = error.get("kind")
    if kind == "cancelled":
        raise AgentRuntimeCancelledError
    if kind == "provider":
        raise AgentRuntimeProviderError(
            code=str(error.get("code")),
            message=str(error.get("message")),
            retryable=error.get("retryable") is True,
        )
    if kind == "policy":
        metadata = error.get("metadata")
        raise AgentRuntimePolicyError(
            code=str(error.get("code")),
            message=str(error.get("message")),
            event_type=str(error.get("event_type")),
            metadata=metadata if isinstance(metadata, dict) else {},
            retryable=error.get("retryable") is True,
        )
    raise RuntimeError(str(error.get("code", "sdk_host_failed")))
