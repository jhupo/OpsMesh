from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.core.security.secrets import SecretEncryptionService
from backend.app.domains.agents.memory.context import AgentMemoryContext, AgentMemoryContextService
from backend.app.domains.agents.memory.models import WorkspaceMemoryEntry
from backend.app.domains.agents.memory.policy import context_budget_policy, working_memory_policy
from backend.app.domains.agents.memory.working import (
    AgentWorkingMemoryService,
    working_memory_context,
)
from backend.app.domains.agents.profiles.models import AgentProfile
from backend.app.domains.agents.runtime.contracts import (
    AgentInputAttachment,
    AgentRunRequest,
    AgentRuntimeAgentTool,
    AgentRuntimeContext,
    AgentRuntimeProfile,
    AgentRuntimeResourceGrant,
    AgentRuntimeToolContinuation,
    AgentRuntimeToolDefinition,
    AgentRuntimeToolExecutor,
    AgentRunTracing,
)
from backend.app.domains.agents.runtime.guardrails import runtime_controls_from_snapshot
from backend.app.domains.agents.runtime.state import AgentRunStateStore
from backend.app.domains.agents.runtime.tools.executor import BackendToolExecutor
from backend.app.domains.agents.sessions.models import PersistentAgentSessionRef
from backend.app.domains.agents.sessions.store import SQLAlchemyAgentSession
from backend.app.domains.capabilities.mcp.transport.resolver import McpAdapterResolver
from backend.app.domains.orchestration.approvals.pending_tools import PendingToolInvocationService
from backend.app.domains.orchestration.requests.attachments import message_attachments
from backend.app.domains.orchestration.requests.context import RunRequestContextProvider
from backend.app.domains.orchestration.requests.context_budget import (
    ContextBudgetManager,
    ContextBudgetResult,
    ContextFragment,
    ContextPriority,
)
from backend.app.domains.orchestration.requests.model_provider import RunRequestModelProviderService
from backend.app.domains.orchestration.requests.prompt import (
    RunRequestPromptRenderer,
)
from backend.app.domains.orchestration.requests.sessions import RunRequestSessionService
from backend.app.domains.orchestration.requests.tracing import agent_run_tracing
from backend.app.domains.orchestration.runs.authorization.runtime import (
    ResolvedRunRuntimeBinding,
    RunRuntimeAuthorizationService,
)
from backend.app.domains.orchestration.runs.authorization.tools import hydrate_agent_tools
from backend.app.domains.orchestration.runs.authorization.validation import (
    RunAuthorizationService,
    agent_runtime_profile_for_snapshot,
    authorized_profile_for_run,
    authorized_task_for_run,
    file_scope_ids_for_snapshot,
    resource_grants_for_snapshot,
    tool_continuations_for_run,
    tool_definitions_for_snapshot,
)
from backend.app.domains.orchestration.runs.cancellation import DatabaseRunCancellation
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.runs.queries import authorization_snapshot_for_run
from backend.app.domains.orchestration.runs.runtime_metadata import RunRuntimeMetadataBuilder
from backend.app.domains.orchestration.tasks.models import Task
from backend.app.domains.workspace.projects.io.support import project_runtime_context
from backend.app.runtime.contracts import SandboxBinding, SandboxManifest
from backend.app.runtime.environment.backends.registry import RuntimeBackendRegistry
from backend.app.runtime.environment.contracts import DockerRuntimeClient
from backend.app.runtime.environment.models import WorkspaceRuntime
from backend.app.runtime.workers.contracts import JobPayload, JobType


@dataclass(frozen=True, slots=True)
class _AuthorizedRequestInputs:
    task: Task | None
    profile: AgentProfile
    runtime_profile: AgentRuntimeProfile
    snapshot: dict[str, object]
    allowed_tools: tuple[str, ...]
    tool_definitions: tuple[AgentRuntimeToolDefinition, ...]
    resource_grants: tuple[AgentRuntimeResourceGrant, ...]
    file_scope_ids: tuple[UUID, ...]
    runtime_binding: ResolvedRunRuntimeBinding
    model_provider: dict[str, Any]


@dataclass(slots=True)
class _RuntimeRequestState:
    metadata: dict[str, object]
    project_workspace: dict[str, object] | None
    sandbox: SandboxBinding | None


@dataclass(slots=True)
class _MemoryRequestState:
    persistent_session_ref: PersistentAgentSessionRef
    working_entries: list[WorkspaceMemoryEntry]
    retrieved_memory: AgentMemoryContext
    persistent_session: SQLAlchemyAgentSession | None
    provider_continuation: dict[str, str | None]
    continuations: tuple[AgentRuntimeToolContinuation, ...]


@dataclass(slots=True)
class _ContextRequestState:
    attachments: tuple[AgentInputAttachment, ...]
    runtime_context: AgentRuntimeContext
    agent_tools: tuple[AgentRuntimeAgentTool, ...]
    output_schema: Any
    guardrails: Any
    budget: ContextBudgetResult
    tracing: AgentRunTracing


@dataclass(slots=True)
class RunRequestBuilder:
    session: Session
    settings: Settings | None
    docker_client: DockerRuntimeClient | None = None
    runtime_backends: RuntimeBackendRegistry | None = None

    @property
    def authorization(self) -> RunAuthorizationService:
        return RunAuthorizationService(self.session)

    @property
    def context_provider(self) -> RunRequestContextProvider:
        return RunRequestContextProvider(self.session)

    @property
    def prompt_renderer(self) -> RunRequestPromptRenderer:
        return RunRequestPromptRenderer(self.session)

    @property
    def session_service(self) -> RunRequestSessionService:
        return RunRequestSessionService(self.session)

    @property
    def model_providers(self) -> RunRequestModelProviderService:
        return RunRequestModelProviderService(self.session, self.settings)

    @property
    def metadata_builder(self) -> RunRuntimeMetadataBuilder:
        return RunRuntimeMetadataBuilder(self.authorization, self.context_provider)

    def build_agent_request(
        self,
        run: AgentRun,
        job: JobPayload,
        model_provider_override: dict[str, Any] | None = None,
    ) -> AgentRunRequest:
        self.validate_job_scope(run, job)
        inputs = self._load_authorized_inputs(run, model_provider_override)
        runtime = self._build_runtime_state(run, inputs)
        memory = self._build_memory_state(run, inputs, runtime.metadata)
        context = self._build_context_state(run, job, inputs, runtime, memory)
        return self._assemble_agent_request(run, inputs, runtime, memory, context)

    def _load_authorized_inputs(
        self,
        run: AgentRun,
        model_provider_override: dict[str, Any] | None,
    ) -> _AuthorizedRequestInputs:
        task = authorized_task_for_run(self.session, run)
        live_profile = authorized_profile_for_run(self.session, run)
        snapshot = authorization_snapshot_for_run(run)
        self.validate_authorization_snapshot(run, task, live_profile, snapshot)
        runtime_profile = agent_runtime_profile_for_snapshot(
            snapshot,
            workspace_id=run.workspace_id,
            profile_id=run.agent_profile_id,
        )
        profile = live_profile
        if profile is None:
            profile = AgentProfile(
                workspace_id=run.workspace_id,
                name=runtime_profile.name,
                role=runtime_profile.role,
                instructions=runtime_profile.instructions,
                model=runtime_profile.model,
                model_settings=dict(runtime_profile.model_settings),
            )
        runtime_binding = RunRuntimeAuthorizationService(self.session).validate_for_run(
            run=run,
            task=task,
            snapshot=snapshot,
        )
        return _AuthorizedRequestInputs(
            task=task,
            profile=profile,
            runtime_profile=runtime_profile,
            snapshot=snapshot,
            allowed_tools=self.allowed_tools_for_run(run),
            tool_definitions=tool_definitions_for_snapshot(snapshot),
            resource_grants=resource_grants_for_snapshot(snapshot),
            file_scope_ids=file_scope_ids_for_snapshot(snapshot),
            runtime_binding=runtime_binding,
            model_provider=self.model_provider_for_run(
                run,
                override=model_provider_override,
            ),
        )

    def _build_runtime_state(
        self,
        run: AgentRun,
        inputs: _AuthorizedRequestInputs,
    ) -> _RuntimeRequestState:
        mode = _runtime_execution_mode(run)
        metadata = self.runtime_metadata(
            run=run,
            task=inputs.task,
            profile=inputs.profile,
            runtime_profile=inputs.runtime_profile,
            model_provider=inputs.model_provider,
            authorization_snapshot=inputs.snapshot,
        )
        binding = inputs.runtime_binding
        runtime_id = (
            binding.workspace_runtime_id if mode == "persistent" else binding.execution_runtime_id
        )
        if runtime_id is not None:
            metadata["runtime_execution"] = {
                "mode": mode,
                "execution_runtime_id": str(runtime_id),
                "parent_runtime_id": (
                    str(binding.workspace_runtime_id)
                    if binding.workspace_runtime_id is not None
                    else None
                ),
            }
        project_workspace = project_runtime_context(self.session, run)
        if project_workspace is not None:
            metadata["project_workspace"] = project_workspace
        sandbox = None
        if runtime_id is not None and inputs.model_provider["provider"] in {
            "openai",
            "openai-compatible",
        }:
            runtime = self.session.scalar(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.id == runtime_id,
                    WorkspaceRuntime.workspace_id == run.workspace_id,
                )
            )
            if runtime is None:
                raise ValueError("Authorized execution runtime is missing")
            root = (
                project_workspace.get("working_directory")
                if isinstance(project_workspace, dict)
                else "/workspace"
            )
            manifest = SandboxManifest(run_id=run.id, root=str(root))
            backend = self._runtime_backends().require(runtime.runtime_provider)
            sandbox_session = backend.sandbox_session(
                manifest,
                runtime,
            )
            sandbox_metadata: dict[str, object] = {
                "session_id": sandbox_session.session_id,
                "root": sandbox_session.root,
                "backend": sandbox_session.backend,
                "persistent": sandbox_session.persistent,
            }
            metadata["sandbox_session"] = sandbox_metadata
            sandbox = SandboxBinding(manifest=manifest, session=sandbox_session)
        if mode == "none":
            metadata["sandbox_mode"] = "none"
        elif runtime_id is None:
            raise ValueError("Sandbox execution requires an authorized runtime session")
        return _RuntimeRequestState(
            metadata=metadata,
            project_workspace=project_workspace,
            sandbox=sandbox,
        )

    def _build_memory_state(
        self,
        run: AgentRun,
        inputs: _AuthorizedRequestInputs,
        metadata: dict[str, object],
    ) -> _MemoryRequestState:
        session_ref = self.persistent_session_ref_for_run(
            run,
            inputs.task,
            inputs.profile,
        )
        working_policy = working_memory_policy(inputs.snapshot.get("memory_policy"))
        working_entries = AgentWorkingMemoryService(self.session).prepare_run(
            run=run,
            profile=inputs.profile,
            task=inputs.task,
            session_key=session_ref.session_key,
            policy=working_policy,
        )
        metadata["working_memory"] = {
            "enabled": working_policy.enabled,
            "entry_count": len(working_entries),
            "entry_ids": [str(entry.id) for entry in working_entries],
            "policy": working_policy.model_dump(mode="json"),
        }
        retrieved_memory = AgentMemoryContextService(
            self.session,
            self.mcp_secret_service(),
        ).build(
            run=run,
            task=inputs.task,
            profile=inputs.profile,
            resource_grants=inputs.resource_grants,
            memory_policy=inputs.snapshot.get("memory_policy"),
        )
        metadata["memory_retrieval"] = retrieved_memory.evidence
        persistent_session = self.persistent_session_for_run(
            run,
            inputs.task,
            inputs.profile,
            ref=session_ref,
        )
        if persistent_session is not None:
            metadata["persistent_session_key"] = persistent_session.session_id
            metadata["persistent_session_mode"] = "sdk_session"
        provider_continuation = self.provider_continuation_for_run(
            run=run,
            session_ref=session_ref,
        )
        if provider_continuation["previous_response_id"] is not None:
            metadata["previous_response_id"] = provider_continuation["previous_response_id"]
        if provider_continuation["conversation_id"] is not None:
            metadata["conversation_id"] = provider_continuation["conversation_id"]
        continuations = tool_continuations_for_run(run.input)
        if continuations:
            metadata["tool_continuations"] = [
                {
                    "tool_name": item.tool_name,
                    "status": item.status,
                    "metadata": item.metadata,
                }
                for item in continuations
            ]
        return _MemoryRequestState(
            persistent_session_ref=session_ref,
            working_entries=working_entries,
            retrieved_memory=retrieved_memory,
            persistent_session=persistent_session,
            provider_continuation=provider_continuation,
            continuations=continuations,
        )

    def _build_context_state(
        self,
        run: AgentRun,
        job: JobPayload,
        inputs: _AuthorizedRequestInputs,
        runtime: _RuntimeRequestState,
        memory: _MemoryRequestState,
    ) -> _ContextRequestState:
        runtime_context = AgentRuntimeContext(
            workspace_id=run.workspace_id,
            task_id=run.task_id,
            run_id=run.id,
            user_id=job.requested_by_user_id,
            allowed_tools=inputs.allowed_tools,
            tool_definitions=inputs.tool_definitions,
            resource_grants=inputs.resource_grants,
            file_scope_ids=inputs.file_scope_ids,
            runtime_binding=inputs.runtime_binding.as_runtime_context(),
            metadata=runtime.metadata,
        )
        agent_tools = hydrate_agent_tools(
            session=self.session,
            snapshot=inputs.snapshot,
            root_context=runtime_context,
            resolve_model_provider=self.resolve_model_provider,
        )
        output_schema, guardrails = runtime_controls_from_snapshot(inputs.snapshot)
        attachments = message_attachments(
            self.session, inputs.task, self.settings or get_settings()
        )
        fragments = self.prompt_renderer.context_fragments_for_run(
            run,
            allowed_tools=inputs.allowed_tools,
            runtime_metadata=runtime.metadata,
        )
        for attachment in attachments:
            if attachment.kind == "audio":
                text = "Voice transcript:\n" + attachment.transcript
            elif (
                attachment.content_type.startswith("text/")
                or attachment.content_type == "application/json"
            ):
                text = "Attached document:\n" + attachment.content.decode("utf-8-sig")
            else:
                continue
            fragments += (
                ContextFragment(
                    key=f"attachment.{attachment.file_id}",
                    text=text,
                    priority=ContextPriority.CRITICAL,
                    required=True,
                ),
            )
        rendered_working_memory = working_memory_context(memory.working_entries)
        if rendered_working_memory:
            fragments += (
                ContextFragment(
                    key="memory.working",
                    text=rendered_working_memory,
                    priority=ContextPriority.HIGH,
                ),
            )
        if memory.retrieved_memory.text:
            fragments += (
                ContextFragment(
                    key="memory.retrieved",
                    text=memory.retrieved_memory.text,
                    priority=ContextPriority.NORMAL,
                ),
            )
        budget = ContextBudgetManager().build(
            fragments=fragments,
            provider=inputs.model_provider["provider"],
            model=inputs.model_provider["model"],
            policy=context_budget_policy(inputs.snapshot.get("memory_policy")),
            instructions=inputs.runtime_profile.instructions,
            tool_definitions=inputs.tool_definitions,
            continuations=memory.continuations,
            agent_tools=agent_tools,
            output_schema=output_schema,
        )
        runtime.metadata["context_budget"] = budget.evidence()
        retrieval_evidence = runtime.metadata["memory_retrieval"]
        if isinstance(retrieval_evidence, dict):
            retrieval_decision = next(
                (item for item in budget.decisions if item.key == "memory.retrieved"),
                None,
            )
            retrieval_evidence.update(
                {
                    "context_status": retrieval_decision.status
                    if retrieval_decision is not None
                    else "not_applicable",
                    "context_estimated_tokens": retrieval_decision.estimated_tokens
                    if retrieval_decision is not None
                    else 0,
                    "context_included_tokens": retrieval_decision.included_tokens
                    if retrieval_decision is not None
                    else 0,
                }
            )
        tracing = agent_run_tracing(
            run=run,
            task=inputs.task,
            profile=inputs.runtime_profile,
            allowed_tools=inputs.allowed_tools,
            metadata=runtime.metadata,
        )
        return _ContextRequestState(
            attachments=attachments,
            runtime_context=runtime_context,
            agent_tools=agent_tools,
            output_schema=output_schema,
            guardrails=guardrails,
            budget=budget,
            tracing=tracing,
        )

    def _assemble_agent_request(
        self,
        run: AgentRun,
        inputs: _AuthorizedRequestInputs,
        runtime: _RuntimeRequestState,
        memory: _MemoryRequestState,
        context: _ContextRequestState,
    ) -> AgentRunRequest:
        secret_service = self.secret_service()
        return AgentRunRequest(
            agent_profile=inputs.runtime_profile,
            input_text=context.budget.text,
            attachments=context.attachments,
            context=context.runtime_context,
            model=inputs.model_provider["model"],
            provider=inputs.model_provider["provider"],
            base_url=inputs.model_provider["base_url"],
            api_key=inputs.model_provider["api_key"],
            model_api=inputs.model_provider["model_api"],
            model_provider_credential_id=inputs.model_provider["model_provider_credential_id"],
            tool_executor=self.build_tool_executor() if inputs.allowed_tools else None,
            continuations=memory.continuations,
            session=memory.persistent_session,
            previous_response_id=memory.provider_continuation["previous_response_id"],
            conversation_id=memory.provider_continuation["conversation_id"],
            tracing=context.tracing,
            sandbox=runtime.sandbox,
            resume_state=AgentRunStateStore(self.session, secret_service).load(
                workspace_id=run.workspace_id,
                run_id=run.id,
            ),
            approval_decisions=PendingToolInvocationService(
                self.session,
                secret_service,
            ).decisions_for_run(
                workspace_id=run.workspace_id,
                run_id=run.id,
            ),
            agent_tools=context.agent_tools,
            output_schema=context.output_schema,
            guardrails=context.guardrails,
            stream=True,
            cancellation=DatabaseRunCancellation.for_session(
                self.session,
                workspace_id=run.workspace_id,
                run_id=run.id,
            ),
        )

    def build_direct_tool_context(
        self,
        run: AgentRun,
        job: JobPayload,
    ) -> tuple[AgentRuntimeContext, AgentRuntimeToolExecutor]:
        """Build frozen authorization for direct workflow tools without a model call."""
        self.validate_job_scope(run, job)
        task = authorized_task_for_run(self.session, run)
        profile = authorized_profile_for_run(self.session, run)
        if task is None or profile is None:
            raise ValueError("Direct workflow tools require a scoped task agent")
        snapshot = authorization_snapshot_for_run(run)
        self.validate_authorization_snapshot(run, task, profile, snapshot)
        runtime_binding = RunRuntimeAuthorizationService(self.session).validate_for_run(
            run=run, task=task, snapshot=snapshot
        )
        catalog = snapshot.get("capability_catalog")
        metadata = {
            "authorization_snapshot_fingerprint": snapshot.get("fingerprint"),
            "capability_catalog_fingerprint": (
                catalog.get("fingerprint") if isinstance(catalog, dict) else None
            ),
            "node_execution": "direct_tool",
        }
        mode = _runtime_execution_mode(run)
        runtime_id = (
            runtime_binding.workspace_runtime_id
            if mode == "persistent"
            else runtime_binding.execution_runtime_id
        )
        if runtime_id is not None:
            metadata["runtime_execution"] = {
                "mode": mode,
                "execution_runtime_id": str(runtime_id),
                "parent_runtime_id": (
                    str(runtime_binding.workspace_runtime_id)
                    if runtime_binding.workspace_runtime_id is not None
                    else None
                ),
            }
        context = AgentRuntimeContext(
            workspace_id=run.workspace_id,
            task_id=run.task_id,
            run_id=run.id,
            user_id=job.requested_by_user_id,
            allowed_tools=self.allowed_tools_for_run(run),
            tool_definitions=tool_definitions_for_snapshot(snapshot),
            resource_grants=resource_grants_for_snapshot(snapshot),
            file_scope_ids=file_scope_ids_for_snapshot(snapshot),
            runtime_binding=runtime_binding.as_runtime_context(),
            metadata=metadata,
        )
        return context, self.build_tool_executor()

    def build_tool_executor(self) -> AgentRuntimeToolExecutor:
        return BackendToolExecutor(
            self.session,
            McpAdapterResolver(secret_service=self.mcp_secret_service()),
            settings=self.settings,
            docker_client=self.docker_client,
            secret_service=self.mcp_secret_service(),
        )

    def _runtime_backends(self) -> RuntimeBackendRegistry:
        if self.runtime_backends is None:
            raise RuntimeError("Runtime backend registry was not composed for run execution")
        return self.runtime_backends

    def runtime_metadata(
        self,
        *,
        run: AgentRun,
        task: Task | None,
        profile: AgentProfile,
        runtime_profile: AgentRuntimeProfile,
        model_provider: dict[str, Any],
        authorization_snapshot: dict[str, object],
    ) -> dict[str, object]:
        return self.metadata_builder.build(
            run=run,
            task=task,
            profile=profile,
            runtime_profile=runtime_profile,
            model_provider=model_provider,
            authorization_snapshot=authorization_snapshot,
        )

    def persistent_session_for_run(
        self,
        run: AgentRun,
        task: Task | None,
        profile: AgentProfile,
        ref: PersistentAgentSessionRef | None = None,
    ) -> SQLAlchemyAgentSession | None:
        return self.session_service.persistent_session_for_run(run, task, profile, ref)

    def provider_continuation_for_run(
        self,
        *,
        run: AgentRun,
        session_ref: PersistentAgentSessionRef,
    ) -> dict[str, str | None]:
        return self.session_service.provider_continuation_for_run(
            run=run,
            session_ref=session_ref,
        )

    def latest_completed_run_for_session(
        self,
        run: AgentRun,
        session_ref: PersistentAgentSessionRef,
    ) -> AgentRun | None:
        return self.session_service.latest_completed_run_for_session(run, session_ref)

    def sync_provider_conversation_id(self, run: AgentRun) -> None:
        self.session_service.sync_provider_conversation_id(run)

    def mailbox_context_for_run(
        self,
        run: AgentRun,
        task: Task | None,
        profile: AgentProfile,
    ) -> dict[str, object]:
        return self.context_provider.mailbox_context_for_run(run, task, profile)

    def team_context_for_run(
        self,
        run: AgentRun,
        task: Task | None,
        profile: AgentProfile,
    ) -> dict[str, object]:
        return self.context_provider.team_context_for_run(run, task, profile)

    def persistent_session_ref_for_run(
        self,
        run: AgentRun,
        task: Task | None,
        profile: AgentProfile,
    ) -> PersistentAgentSessionRef:
        return self.session_service.persistent_session_ref_for_run(run, task, profile)

    def validate_job_scope(self, run: AgentRun, job: JobPayload) -> None:
        if job.job_type != JobType.AGENT_RUN:
            raise ValueError("Worker job type does not match agent run execution")
        if job.workspace_id != run.workspace_id:
            raise ValueError("Worker job workspace mismatch")
        if job.resource_id != run.id:
            raise ValueError("Worker job resource does not match agent run")

    def mcp_secret_service(self) -> SecretEncryptionService | None:
        if self.settings is None:
            return None
        return self.secret_service()

    def secret_service(self) -> SecretEncryptionService:
        return self.model_providers.secret_service()

    def model_provider_for_run(
        self,
        run: AgentRun,
        *,
        override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.model_providers.provider_for_run(run, override=override)

    def resolve_model_provider(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID | None,
        agent_model: str,
        model_api: str | None = None,
        prefer_model_api: bool = False,
    ) -> dict[str, Any]:
        return self.model_providers.resolve(
            workspace_id=workspace_id,
            credential_id=credential_id,
            agent_model=agent_model,
            model_api=model_api,
            prefer_model_api=prefer_model_api,
        )

    def input_text_for_run(
        self,
        run: AgentRun,
        *,
        allowed_tools: tuple[str, ...] = (),
        runtime_metadata: dict[str, object] | None = None,
    ) -> str:
        return self.prompt_renderer.input_text_for_run(
            run,
            allowed_tools=allowed_tools,
            runtime_metadata=runtime_metadata,
        )

    def team_context_text_for_run(
        self,
        run: AgentRun,
        task: Task,
        *,
        allowed_tools: tuple[str, ...] = (),
    ) -> str:
        return self.prompt_renderer.team_context_text_for_run(
            run,
            task,
            allowed_tools=allowed_tools,
        )

    def allowed_tools_for_run(
        self,
        run: AgentRun,
    ) -> tuple[str, ...]:
        return self.authorization.allowed_tools_for_run(run)

    def validate_authorization_snapshot(
        self,
        run: AgentRun,
        task: Task | None,
        profile: AgentProfile | None,
        snapshot: dict[str, object],
    ) -> None:
        self.authorization.validate_authorization_snapshot(run, task, profile, snapshot)

    def step_context_for_run(self, run: AgentRun) -> dict[str, object]:
        return self.authorization.step_context_for_run(run)

    def installed_skill_snapshots(
        self,
        workspace_id: UUID,
        profile: AgentProfile | None,
    ) -> list[dict[str, object]]:
        return self.authorization.installed_skill_snapshots(workspace_id, profile)

    def installed_skill_mcp_tool_snapshots(self, workspace_id: UUID) -> list[dict[str, object]]:
        return self.authorization.installed_skill_mcp_tool_snapshots(workspace_id)

    def mcp_credential_reference_snapshots(
        self,
        workspace_id: UUID,
    ) -> list[dict[str, object]]:
        return self.authorization.mcp_credential_reference_snapshots(workspace_id)


def _runtime_execution_mode(run: AgentRun) -> str:
    metadata = run.input.get("runtime_execution") if isinstance(run.input, dict) else None
    if not isinstance(metadata, dict):
        return "none"
    mode = metadata.get("mode")
    return mode if isinstance(mode, str) else "none"
