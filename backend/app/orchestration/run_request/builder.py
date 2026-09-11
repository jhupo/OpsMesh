from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.core.contracts import (
    AgentRunRequest,
    AgentRuntimeContext,
    AgentRuntimeToolExecutor,
)
from backend.app.agent_runtime.guardrails import runtime_controls_from_snapshot
from backend.app.agent_runtime.sandbox.contracts import SandboxManifest
from backend.app.agent_runtime.sandbox.mapper import (
    manifest_to_openai_run_config,
    sandbox_settings_for_claude,
)
from backend.app.agent_runtime.sessions import (
    PersistentAgentSessionRef,
    SQLAlchemyAgentSession,
)
from backend.app.agent_runtime.state_store import AgentRunStateStore
from backend.app.agent_runtime.tools import BackendToolExecutor
from backend.app.agents.memory_policy import context_budget_policy, working_memory_policy
from backend.app.agents.models import AgentProfile
from backend.app.approvals.pending_tools import PendingToolInvocationService
from backend.app.capabilities.mcp.adapter_resolver import McpAdapterResolver
from backend.app.core.config import Settings
from backend.app.memory.context import AgentMemoryContextService
from backend.app.memory.working import AgentWorkingMemoryService, working_memory_context
from backend.app.projects.runtime_context import project_runtime_context
from backend.app.runs.models import AgentRun
from backend.app.runtime_manager.backends.registry import build_runtime_backend_registry
from backend.app.runtime_manager.contracts import DockerRuntimeClient
from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.secrets.service import SecretEncryptionService
from backend.app.tasks.models import Task
from backend.app.workers.jobs import JobPayload, JobType

from ..state.context_budget import ContextBudgetManager, ContextFragment, ContextPriority
from ..run_agent_tool_authorization import hydrate_agent_tools
from ..run_cancellation import DatabaseRunCancellation
from ..runtime.authorization import RunRuntimeAuthorizationService
from ..runtime.metadata import RunRuntimeMetadataBuilder
from .authorization import (
    RunAuthorizationService,
    file_scope_ids_for_snapshot,
    resource_grants_for_snapshot,
    tool_continuations_for_run,
    tool_definitions_for_snapshot,
)
from .context import RunRequestContextProvider
from .model_provider import RunRequestModelProviderService
from .prompt import (
    RunRequestPromptRenderer,
)
from .sessions import RunRequestSessionService
from .tracing import agent_run_tracing


@dataclass(slots=True)
class RunRequestBuilder:
    session: Session
    settings: Settings | None
    docker_client: DockerRuntimeClient | None = None

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
        task = self.authorized_task_for_run(run)
        profile = self.authorized_profile_for_run(run)
        if profile is None:
            profile = AgentProfile(
                workspace_id=run.workspace_id,
                name="Default Agent",
                role="worker",
                instructions="Complete the assigned task.",
                model=run.model or "gpt-4.1",
            )

        authorization_snapshot = self.authorization_snapshot_for_run(run)
        self.validate_authorization_snapshot(run, task, profile, authorization_snapshot)
        allowed_tools = self.allowed_tools_for_run(run, profile)
        tool_definitions = tool_definitions_for_snapshot(authorization_snapshot)
        resource_grants = resource_grants_for_snapshot(authorization_snapshot)
        file_scope_ids = file_scope_ids_for_snapshot(authorization_snapshot)
        runtime_binding = RunRuntimeAuthorizationService(self.session).validate_for_run(
            run=run,
            task=task,
            snapshot=authorization_snapshot,
        )
        model_provider = self.model_provider_for_run(
            run,
            profile,
            override=model_provider_override,
        )
        metadata = self.runtime_metadata(
            run=run,
            task=task,
            profile=profile,
            model_provider=model_provider,
            authorization_snapshot=authorization_snapshot,
        )
        if (
            runtime_binding.execution_runtime_id is not None
            or _runtime_execution_mode(run) == "persistent"
        ):
            metadata["runtime_execution"] = {
                "mode": _runtime_execution_mode(run),
                "execution_runtime_id": str(runtime_binding.execution_runtime_id),
                "parent_runtime_id": (
                    str(runtime_binding.workspace_runtime_id)
                    if runtime_binding.workspace_runtime_id is not None
                    else None
                ),
            }
        metadata["sandbox_settings"] = sandbox_settings_for_claude(
            network_disabled=runtime_binding.network_disabled
        ) if _runtime_execution_mode(run) != "none" else {"enabled": False}
        project_workspace = project_runtime_context(self.session, run)
        if project_workspace is not None:
            metadata["project_workspace"] = project_workspace
        if runtime_binding.execution_runtime_id is not None:
            runtime = self.session.scalar(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.id == runtime_binding.execution_runtime_id,
                    WorkspaceRuntime.workspace_id == run.workspace_id,
                )
            )
            if runtime is None:
                raise ValueError("Authorized execution runtime is missing")
            metadata["sandbox_session"] = {
                "session_id": str(runtime_binding.execution_runtime_id),
                "root": (
                    project_workspace.get("working_directory")
                    if isinstance(project_workspace, dict)
                    else "/workspace"
                ),
                "backend": "runtime_manager",
                "persistent": _runtime_execution_mode(run) == "persistent",
            }
            backend = build_runtime_backend_registry(
                self.session, self.docker_client, self.secret_service()
            ).resolve(runtime.runtime_provider)
            if backend is not None and hasattr(backend, "sandbox_session"):
                session = backend.sandbox_session(
                    SandboxManifest(run_id=run.id, root=str(metadata["sandbox_session"]["root"])),
                    runtime,
                )
                metadata["sandbox_session"] = {
                    "session_id": session.session_id,
                    "root": session.root,
                    "backend": session.backend,
                    "persistent": session.persistent,
                }
                if model_provider["provider"] == "anthropic" and hasattr(backend, "sdk_process"):
                    process = backend.sdk_process(session)
                    metadata["sandbox_session"]["cli_path"] = (
                        process.install_claude_cli_wrapper()
                    )
        persistent_session_ref = self.persistent_session_ref_for_run(run, task, profile)
        working_policy = working_memory_policy(authorization_snapshot.get("memory_policy"))
        working_entries = AgentWorkingMemoryService(self.session).prepare_run(
            run=run,
            profile=profile,
            task=task,
            session_key=persistent_session_ref.session_key,
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
            task=task,
            profile=profile,
            resource_grants=resource_grants,
            memory_policy=authorization_snapshot.get("memory_policy"),
        )
        metadata["memory_retrieval"] = retrieved_memory.evidence
        persistent_session = self.persistent_session_for_run(
            run,
            task,
            profile,
            ref=persistent_session_ref,
        )
        if persistent_session is not None:
            metadata["persistent_session_key"] = persistent_session.session_id
            metadata["persistent_session_mode"] = "sdk_session"

        provider_continuation = self.provider_continuation_for_run(
            run=run,
            session_ref=persistent_session_ref,
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

        runtime_context = AgentRuntimeContext(
            workspace_id=run.workspace_id,
            task_id=run.task_id,
            run_id=run.id,
            user_id=job.requested_by_user_id,
            allowed_tools=allowed_tools,
            tool_definitions=tool_definitions,
            resource_grants=resource_grants,
            file_scope_ids=file_scope_ids,
            runtime_binding=runtime_binding.as_runtime_context(),
            metadata=metadata,
        )
        execution_mode = _runtime_execution_mode(run)
        if execution_mode == "none":
            metadata["sandbox_mode"] = "none"
        agent_tools = hydrate_agent_tools(
            session=self.session,
            snapshot=authorization_snapshot,
            root_context=runtime_context,
            resolve_model_provider=self.resolve_model_provider,
        )
        output_schema, guardrails = runtime_controls_from_snapshot(authorization_snapshot)
        context_fragments = self.prompt_renderer.context_fragments_for_run(
            run,
            allowed_tools=allowed_tools,
            runtime_metadata=metadata,
        )
        rendered_working_memory = working_memory_context(working_entries)
        if rendered_working_memory:
            context_fragments += (
                ContextFragment(
                    key="memory.working",
                    text=rendered_working_memory,
                    priority=ContextPriority.HIGH,
                ),
            )
        if retrieved_memory.text:
            context_fragments += (
                ContextFragment(
                    key="memory.retrieved",
                    text=retrieved_memory.text,
                    priority=ContextPriority.NORMAL,
                ),
            )
        context_budget = ContextBudgetManager().build(
            fragments=context_fragments,
            provider=model_provider["provider"],
            model=model_provider["model"],
            policy=context_budget_policy(authorization_snapshot.get("memory_policy")),
            instructions=profile.instructions,
            tool_definitions=tool_definitions,
            continuations=continuations,
            agent_tools=agent_tools,
            output_schema=output_schema,
        )
        metadata["context_budget"] = context_budget.evidence()
        retrieval_evidence = metadata["memory_retrieval"]
        if isinstance(retrieval_evidence, dict):
            retrieval_decision = next(
                (item for item in context_budget.decisions if item.key == "memory.retrieved"),
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
            task=task,
            profile=profile,
            allowed_tools=allowed_tools,
            metadata=metadata,
        )
        sandbox = None
        if _runtime_execution_mode(run) != "none" and model_provider["provider"] in {
            "openai",
            "openai-compatible",
        }:
            workspace = metadata.get("project_workspace")
            root = (
                workspace.get("working_directory")
                if isinstance(workspace, dict)
                else "/workspace"
            )
            sandbox = manifest_to_openai_run_config(
                SandboxManifest(run_id=run.id, root=str(root))
            )
        return AgentRunRequest(
            agent_profile=profile,
            input_text=context_budget.text,
            context=runtime_context,
            model=model_provider["model"],
            provider=model_provider["provider"],
            base_url=model_provider["base_url"],
            api_key=model_provider["api_key"],
            model_api=model_provider["model_api"],
            model_provider_credential_id=model_provider["model_provider_credential_id"],
            tool_executor=self.build_tool_executor() if allowed_tools else None,
            continuations=continuations,
            session=persistent_session,
            previous_response_id=provider_continuation["previous_response_id"],
            conversation_id=provider_continuation["conversation_id"],
            tracing=tracing,
            sandbox=sandbox,
            resume_state=AgentRunStateStore(
                self.session,
                self.secret_service(),
            ).load(
                workspace_id=run.workspace_id,
                run_id=run.id,
            ),
            approval_decisions=PendingToolInvocationService(
                self.session,
                self.secret_service(),
            ).decisions_for_run(
                workspace_id=run.workspace_id,
                run_id=run.id,
            ),
            agent_tools=agent_tools,
            output_schema=output_schema,
            guardrails=guardrails,
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
        task = self.authorized_task_for_run(run)
        profile = self.authorized_profile_for_run(run)
        if task is None or profile is None:
            raise ValueError("Direct workflow tools require a scoped task agent")
        snapshot = self.authorization_snapshot_for_run(run)
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
        if (
            runtime_binding.execution_runtime_id is not None
            or _runtime_execution_mode(run) == "persistent"
        ):
            metadata["runtime_execution"] = {
                "mode": _runtime_execution_mode(run),
                "execution_runtime_id": str(runtime_binding.execution_runtime_id),
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
            allowed_tools=self.allowed_tools_for_run(run, profile),
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

    def runtime_metadata(
        self,
        *,
        run: AgentRun,
        task: Task | None,
        profile: AgentProfile,
        model_provider: dict[str, Any],
        authorization_snapshot: dict[str, object],
    ) -> dict[str, object]:
        return self.metadata_builder.build(
            run=run,
            task=task,
            profile=profile,
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

    def authorized_task_for_run(self, run: AgentRun) -> Task | None:
        if run.task_id is None:
            return None
        task = self.session.get(Task, run.task_id)
        if task is None:
            raise ValueError("Run task not found")
        if task.workspace_id != run.workspace_id:
            raise ValueError("Run task workspace mismatch")
        return task

    def authorized_profile_for_run(self, run: AgentRun) -> AgentProfile | None:
        if run.agent_profile_id is None:
            return None
        profile = self.session.get(AgentProfile, run.agent_profile_id)
        if profile is None:
            raise ValueError("Run agent profile not found")
        if profile.workspace_id != run.workspace_id:
            raise ValueError("Run agent profile workspace mismatch")
        return profile

    def model_provider_for_run(
        self,
        run: AgentRun,
        profile: AgentProfile,
        *,
        override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.model_providers.provider_for_run(run, profile, override=override)

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
        profile: AgentProfile,
    ) -> tuple[str, ...]:
        return self.authorization.allowed_tools_for_run(run, profile)

    def authorization_snapshot_for_run(self, run: AgentRun) -> dict[str, object]:
        return self.authorization.authorization_snapshot_for_run(run)

    def validate_authorization_snapshot(
        self,
        run: AgentRun,
        task: Task | None,
        profile: AgentProfile,
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
        return "isolated"
    mode = metadata.get("mode")
    return mode if isinstance(mode, str) else "isolated"
