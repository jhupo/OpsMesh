"""Map product workspace declarations only at the OpenAI SDK boundary."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path, PurePosixPath
from typing import BinaryIO, cast
from uuid import UUID

from agents.models.openai_provider import OpenAIProvider
from agents.sandbox import Manifest, SandboxRunConfig
from agents.sandbox.capabilities import Filesystem, Memory, Shell
from agents.sandbox.capabilities.capability import Capability
from agents.sandbox.config import MemoryGenerateConfig, MemoryLayoutConfig, MemoryReadConfig
from agents.sandbox.errors import WorkspaceReadNotFoundError
from agents.sandbox.manifest import Environment
from agents.sandbox.session import BaseSandboxSession, SandboxSessionState
from agents.sandbox.snapshot import NoopSnapshot
from agents.sandbox.types import ExecResult, User

from opsmesh.agents.execution.contracts import AgentRunRequest
from opsmesh.agents.execution.providers.openai.memory_usage import (
    MeteredMemoryModel,
    memory_usage,
)
from opsmesh.agents.execution.providers.openai.settings import OpenAIModelSettingsMapper
from opsmesh.agents.providers.model_api import OPENAI_RESPONSES_API, canonical_model_api
from opsmesh.resources.memory.policy import SDKMemoryPolicy
from opsmesh.runtime.contracts import SandboxBinding


class OpsMeshSandboxSession(BaseSandboxSession):
    """Expose an OpsMesh-owned runtime as a live OpenAI Agents SDK session.

    The SDK owns agent capabilities and filesystem/shell tool behavior. OpsMesh retains
    ownership of container allocation, pooling, policy, cleanup, and durable evidence.
    """

    def __init__(self, binding: SandboxBinding) -> None:
        self._binding = binding
        manifest = Manifest(
            root=binding.manifest.root,
            environment=Environment(value=dict(binding.manifest.environment)),
        )
        self.state = SandboxSessionState(
            type="opsmesh",
            session_id=binding.manifest.run_id,
            snapshot=NoopSnapshot(id=str(binding.manifest.run_id)),
            manifest=manifest,
            workspace_root_ready=True,
        )

    async def start(self) -> None:
        """Verify the worker-owned container before the SDK starts capability setup."""

        if not await self.running():
            raise RuntimeError("OpsMesh sandbox runtime is not running")
        await super().start()

    async def _exec_internal(
        self,
        *command: str | Path,
        timeout: float | None = None,
    ) -> ExecResult:
        result = await asyncio.to_thread(
            self._binding.session.executor.execute,
            [str(part) for part in command],
            timeout_seconds=max(1, int(timeout or 300)),
            working_dir=self.state.manifest.root,
        )
        return ExecResult(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
        )

    async def _validate_path_access(
        self,
        path: Path | str,
        *,
        for_write: bool = False,
    ) -> Path:
        """Validate the normalized path against the live container filesystem.

        The SDK's base implementation only normalizes paths locally.  An OpsMesh runtime is a
        remote Docker filesystem, so the provider helper must also resolve symlinks and enforce
        the workspace boundary inside that container before any read or write is attempted.
        """

        return await self._validate_remote_path_access(path, for_write=for_write)

    async def read(self, path: Path, *, user: str | User | None = None) -> io.IOBase:
        del user
        try:
            resolved = await self._check_read_with_exec(path)
        except FileNotFoundError as exc:
            raise WorkspaceReadNotFoundError(path=path, cause=exc) from exc
        content = await asyncio.to_thread(
            self._binding.session.executor.read_file,
            PurePosixPath(resolved.as_posix()),
        )
        if content is None:
            raise WorkspaceReadNotFoundError(path=resolved)
        return io.BytesIO(content)

    async def write(
        self,
        path: Path,
        data: io.IOBase,
        *,
        user: str | User | None = None,
    ) -> None:
        del user
        resolved = await self._check_write_with_exec(path)
        await asyncio.to_thread(
            self._binding.session.executor.write_file,
            PurePosixPath(resolved.as_posix()),
            cast(BinaryIO, data),
        )

    async def running(self) -> bool:
        return await asyncio.to_thread(self._binding.session.executor.running)

    async def persist_workspace(self) -> io.IOBase:
        raise RuntimeError("OpsMesh persists project files outside the provider SDK lifecycle")

    async def hydrate_workspace(self, data: io.IOBase) -> None:
        del data
        raise RuntimeError("OpsMesh hydrates project files before the provider SDK run")


def sandbox_run_config(binding: SandboxBinding) -> SandboxRunConfig:
    return SandboxRunConfig(session=OpsMeshSandboxSession(binding))


def sandbox_capabilities(
    request: AgentRunRequest,
    *,
    profile_id: UUID | None = None,
    model_settings: dict[str, object] | None = None,
) -> list[Capability]:
    """SDK Memory owns extraction and consolidation; platform selects its scoped storage."""
    capabilities: list[Capability] = [Filesystem(), Shell()]
    policy = SDKMemoryPolicy.model_validate(request.context.metadata.get("sdk_memory", {}))
    if not policy.enabled:
        return capabilities
    if request.sandbox is None or not request.sandbox.session.persistent:
        raise ValueError("SDK memory requires a persistent Runtime")
    profile_id = profile_id or request.agent_profile.id
    if request.context.user_id is None or profile_id is None:
        raise ValueError("SDK memory requires an authorized user and agent profile")
    if not policy.read and not policy.generate:
        raise ValueError("SDK memory requires reading or generation")
    generate = None
    if policy.generate:
        if request.api_key is None:
            raise ValueError("SDK memory generation requires a frozen provider credential")
        model = OpenAIProvider(
            api_key=request.api_key,
            base_url=request.base_url,
            use_responses=canonical_model_api(request.model_api) == OPENAI_RESPONSES_API,
        ).get_model(request.model or request.agent_profile.model)
        meter = memory_usage.get()
        if meter is not None:
            model = MeteredMemoryModel(model, meter)
        settings = OpenAIModelSettingsMapper().map_settings(
            model_settings if model_settings is not None else request.agent_profile.model_settings
        )
        generate = MemoryGenerateConfig(
            phase_one_model=model,
            phase_two_model=model,
            phase_one_model_settings=settings,
            phase_two_model_settings=settings,
            max_raw_memories_for_consolidation=policy.max_raw_memories,
        )
    scope = f".opsmesh/memory/{request.context.workspace_id}/{request.context.user_id}/{profile_id}"
    capabilities.append(
        Memory(
            layout=MemoryLayoutConfig(
                memories_dir=f"{scope}/memories", sessions_dir=f"{scope}/sessions"
            ),
            read=MemoryReadConfig(live_update=policy.live_update) if policy.read else None,
            generate=generate,
        )
    )
    return capabilities
