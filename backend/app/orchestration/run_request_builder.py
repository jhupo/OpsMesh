from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRuntimeContext,
)
from backend.app.agent_runtime.sessions import (
    PersistentAgentSessionRef,
    SQLAlchemyAgentSession,
)
from backend.app.agent_runtime.tools import BackendToolExecutor
from backend.app.agents.models import AgentProfile
from backend.app.capabilities.mcp_adapter_resolver import McpAdapterResolver
from backend.app.core.config import Settings
from backend.app.runs.models import AgentRun
from backend.app.runtime_manager.contracts import DockerRuntimeClient
from backend.app.secrets.service import SecretEncryptionService
from backend.app.tasks.models import Task
from backend.app.workers.jobs import JobPayload, JobType

from .run_request_authorization import (
    RunAuthorizationService,
    file_scope_ids_for_snapshot,
    resource_grants_for_snapshot,
    tool_continuations_for_run,
    tool_definitions_for_snapshot,
)
from .run_request_context import RunRequestContextProvider
from .run_request_model_provider import RunRequestModelProviderService
from .run_request_prompt import (
    RunRequestPromptRenderer,
)
from .run_request_sessions import RunRequestSessionService
from .run_request_tracing import agent_run_tracing
from .run_runtime_metadata import RunRuntimeMetadataBuilder


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
        persistent_session_ref = self.persistent_session_ref_for_run(run, task, profile)
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

        tracing = agent_run_tracing(
            run=run,
            task=task,
            profile=profile,
            allowed_tools=allowed_tools,
            metadata=metadata,
        )
        return AgentRunRequest(
            agent_profile=profile,
            input_text=self.input_text_for_run(
                run,
                allowed_tools=allowed_tools,
                runtime_metadata=metadata,
            ),
            context=AgentRuntimeContext(
                workspace_id=run.workspace_id,
                task_id=run.task_id,
                run_id=run.id,
                user_id=job.requested_by_user_id,
                allowed_tools=allowed_tools,
                tool_definitions=tool_definitions,
                resource_grants=resource_grants,
                file_scope_ids=file_scope_ids,
                metadata=metadata,
            ),
            model=model_provider["model"],
            provider=model_provider["provider"],
            base_url=model_provider["base_url"],
            api_key=model_provider["api_key"],
            model_api=model_provider["model_api"],
            model_provider_credential_id=model_provider["model_provider_credential_id"],
            tool_executor=BackendToolExecutor.for_mcp_adapter(
                self.session,
                McpAdapterResolver(secret_service=self.mcp_secret_service()),
                settings=self.settings,
                docker_client=self.docker_client,
                secret_service=self.mcp_secret_service(),
            )
            if allowed_tools
            else None,
            continuations=continuations,
            session=persistent_session,
            previous_response_id=provider_continuation["previous_response_id"],
            conversation_id=provider_continuation["conversation_id"],
            tracing=tracing,
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

    def completed_step_summaries(self, task_id: UUID, *, before: int) -> list[str]:
        return self.prompt_renderer.completed_step_summaries(task_id, before=before)
