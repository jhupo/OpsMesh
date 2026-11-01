"""Typed execution services and backend-neutral resource lifecycle contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from .hosted import HostedToolBinding
from .native_tools import NativeToolBinding
from .specification import AgentSpecification, SandboxConfiguration, ToolBinding

if TYPE_CHECKING:
    from .context import AgentExecutionContext

MaterializedFiles = tuple[tuple[str, bytes, bool], ...]
ResourceCheckpoint = Callable[[], Awaitable[dict[str, Any]]]


class PreparedExecutionResources(Protocol):
    @property
    def instructions(self) -> str: ...
    @property
    def tools(self) -> tuple[ToolBinding, ...]: ...


class ModelHistorySession(Protocol):
    async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]: ...
    async def add_items(self, items: list[dict[str, Any]]) -> None: ...
    async def pop_item(self) -> dict[str, Any] | None: ...
    async def clear_session(self) -> None: ...
    async def replace_items(self, items: list[dict[str, Any]]) -> bool: ...


@dataclass
class AgentExecutionServices:
    resolve_secret: Callable[[str], Awaitable[str]] | None = None
    prepare_model_profile: (
        Callable[[AgentSpecification], Awaitable[dict[str, Any] | None]] | None
    ) = None
    prepare_agent_resources: (
        Callable[[AgentSpecification], Awaitable[PreparedExecutionResources]] | None
    ) = None
    observe_usage: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    observe_resource_usage: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    observe_metric: Callable[[str, float], None] | None = None
    authorize_hosted_tool: Callable[[HostedToolBinding], Awaitable[None]] | None = None
    authorize_native_tool: Callable[[NativeToolBinding], Awaitable[None]] | None = None
    authorize_model_request: Callable[[str | None], Awaitable[None]] | None = None
    authorize_workspace: Callable[[str], Awaitable[None]] | None = None
    sync_agent_resources: (
        Callable[[AgentExecutionContext, str, dict[str, Any]], Awaitable[None]] | None
    ) = None
    trace_context: dict[str, str] = field(default_factory=dict)


@dataclass
class AgentExecutionState:
    attempt_id: str | None = None
    usage_snapshot: dict[str, Any] = field(default_factory=dict)
    model_history: ModelHistorySession | None = None
    delegated_artifacts: list[dict[str, Any]] = field(default_factory=list)


class SandboxSession(Protocol):
    @property
    def session(self) -> object: ...
    def cleanup_state(self) -> dict[str, Any]: ...
    async def archive(self) -> bytes: ...
    async def materialize_files(self, files: MaterializedFiles) -> None: ...
    async def close(self) -> None: ...


@dataclass
class AgentExecutionResources:
    sandbox_session: object | None = None
    sandbox_lifecycle: SandboxSession | None = None
    sandbox_root_specification: AgentSpecification | None = None
    sync_resources: Callable[[MaterializedFiles], Awaitable[None]] | None = None
    checkpoint_resources: ResourceCheckpoint | None = None
    cleanup_resources: Callable[[], Awaitable[None]] | None = None
    claim_workspace: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    prepare_delegated_sandbox: (
        Callable[[AgentSpecification, AgentExecutionContext, str], Awaitable[AgentExecutionContext]]
        | None
    ) = None


@dataclass(frozen=True)
class SandboxProviderBinding:
    client: Any
    options: Any = None
    validate_manifest: Callable[[dict[str, Any], AgentExecutionContext], Awaitable[None]] | None = (
        None
    )
    aclose: Callable[[], Awaitable[None]] | None = None


SandboxProviderFactory = Callable[
    [SandboxConfiguration, "AgentExecutionContext"], Awaitable[SandboxProviderBinding]
]


class SandboxSessionFactory(Protocol):
    async def open(
        self,
        configuration: SandboxConfiguration,
        files: MaterializedFiles,
        directories: tuple[str, ...],
        *,
        context: AgentExecutionContext | None = None,
        provider: SandboxProviderBinding | None = None,
        trusted_source: bool = False,
    ) -> SandboxSession: ...
    async def resume_cleanup(
        self,
        configuration: SandboxConfiguration,
        state: dict[str, Any],
        *,
        provider: SandboxProviderBinding | None = None,
    ) -> SandboxSession: ...


class SandboxProviderRegistry(Protocol):
    def register_provider(self, reference: str, factory: SandboxProviderFactory) -> None: ...
