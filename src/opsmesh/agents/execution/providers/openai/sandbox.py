"""Configure the OpenAI Agents SDK with a native Temporal sandbox client."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal
from uuid import UUID

from agents.run_config import SandboxRunConfig
from agents.sandbox.capabilities import Filesystem, Memory, Shell
from agents.sandbox.capabilities.capability import Capability
from agents.sandbox.config import MemoryGenerateConfig, MemoryLayoutConfig, MemoryReadConfig
from agents.sandbox.manifest import Manifest
from agents.sandbox.session import BaseSandboxClient, BaseSandboxSession
from pydantic import PrivateAttr
from temporalio import workflow
from temporalio.common import RetryPolicy

from opsmesh.agents.execution.contracts import AgentRunRequest
from opsmesh.agents.execution.providers.openai.contracts import AgentModelReference
from opsmesh.agents.execution.providers.openai.sandbox_tools import configure_sandbox_tools
from opsmesh.agents.execution.providers.openai.settings import model_settings_from_config
from opsmesh.resources.memory.policy import SDKMemoryPolicy
from opsmesh.workspaces.projects.io.native import ProjectInputEntry, ProjectOutputHarvestRequest


class ProjectFilesCapability(Capability):
    """Declare product project inputs and collect final outputs through SDK hooks."""

    type: Literal["opsmesh_project"] = "opsmesh_project"
    workspace_id: UUID
    run_id: UUID
    backend_id: str
    _binding: list[BaseSandboxSession] = PrivateAttr(default_factory=list)

    def clone(self) -> ProjectFilesCapability:
        # The final SDK run hook receives the Agent's declared capabilities, whereas
        # the SDK binds a per-run clone. Only this product binding is shared with it.
        return self.model_copy(deep=False)

    def bind(self, session: BaseSandboxSession) -> None:
        super().bind(session)
        self._binding[:] = [session]

    def process_manifest(self, manifest: Manifest) -> Manifest:
        return manifest.model_copy(
            update={
                "entries": {
                    **manifest.entries,
                    ".opsmesh/project": ProjectInputEntry(
                        workspace_id=self.workspace_id,
                        run_id=self.run_id,
                    ),
                }
            }
        )

    async def harvest(self) -> None:
        if not self._binding:
            raise ValueError("Project outputs require the SDK's bound native sandbox session")
        # Persist through the native SDK before another worker collects outputs.
        await self._binding[0].stop()
        await workflow.execute_activity(
            "opsmesh.project.harvest",
            ProjectOutputHarvestRequest(
                workspace_id=self.workspace_id,
                run_id=self.run_id,
                backend_id=self.backend_id,
                session_state=self._binding[0].state.model_dump(mode="json"),
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=3),
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )


def sandbox_run_config(
    client: BaseSandboxClient[Any], *, state: dict[str, object] | None
) -> SandboxRunConfig:
    """Build the SDK config from the registered Temporal sandbox client.

    The locked Temporal plugin rejects session=, including its own proxy. Its
    supported session_state= resumes the authorized reference; a public Worker
    interceptor retains compute at the SDK cleanup boundary.
    """
    if state is None:
        raise ValueError("Native sandbox execution requires an authorized session")
    return SandboxRunConfig(
        client=client,
        session_state=client.deserialize_session_state(state),
    )


def sandbox_capabilities(
    request: AgentRunRequest,
    *,
    profile_id: UUID | None = None,
    model_settings: dict[str, object] | None = None,
    approval_reviews: dict[str, dict[str, object]] | None = None,
) -> list[Capability]:
    """Configure SDK-owned filesystem, shell, and optional memory capabilities."""
    configure = configure_sandbox_tools(
        request, approval_reviews if approval_reviews is not None else {}
    )
    capabilities: list[Capability] = [
        Filesystem(configure_tools=configure),
        Shell(configure_tools=configure),
    ]
    if request.context.metadata.get("project_workspace") is not None:
        backend_id = request.context.metadata.get("sandbox_backend_id")
        if not isinstance(backend_id, str):
            raise ValueError("Project files require a registered native sandbox backend")
        capabilities.append(
            ProjectFilesCapability(
                workspace_id=request.context.workspace_id,
                run_id=request.context.run_id,
                backend_id=backend_id,
            )
        )
    policy = SDKMemoryPolicy.model_validate(request.context.metadata.get("sdk_memory", {}))
    if not policy.enabled:
        return capabilities
    if request.sdk_sandbox_client is None:
        raise ValueError("SDK memory requires a native Temporal sandbox client")
    profile_id = profile_id or request.agent_profile.id
    if request.context.user_id is None or profile_id is None:
        raise ValueError("SDK memory requires an authorized user and agent profile")
    if not policy.read and not policy.generate:
        raise ValueError("SDK memory requires reading or generation")
    generate = None
    if policy.generate:
        if request.model_provider_credential_id is None:
            raise ValueError("SDK memory generation requires an authorized credential reference")
        # Memory uses SDK Runner internally. A model reference lets the official
        # Temporal plugin route those calls through its authorized model Activity.
        model = AgentModelReference(
            workspace_id=request.context.workspace_id,
            run_id=request.context.run_id,
            agent_profile_id=profile_id,
            credential_id=request.model_provider_credential_id,
            model=request.model or request.agent_profile.model,
            model_api=request.model_api,
            purpose="memory",
        ).encode()
        settings = model_settings_from_config(
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
