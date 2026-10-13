"""Resolve model credentials only inside the official model Activity."""

import asyncio
import logging
import re
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from traceback import walk_tb
from typing import Any
from uuid import uuid4

from agents import ModelProvider, ModelResponse
from agents.items import TResponseStreamEvent
from agents.models.interface import Model
from agents.models.openai_provider import OpenAIProvider
from agents.usage import Usage
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, OpenAIError
from opentelemetry import trace
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.exceptions import ApplicationError

from opsmesh.agents.execution.contracts import (
    AgentExecutionContext,
    AgentModelUsageRecord,
    AgentProfileSnapshot,
    AgentReference,
    AgentRunRequest,
    AgentRunResult,
)
from opsmesh.agents.execution.model_authorization import AuthorizedModelResolver
from opsmesh.agents.execution.providers.openai.contracts import AgentModelReference
from opsmesh.agents.execution.usage import runtime_usage
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.providers.model_api import OPENAI_RESPONSES_API
from opsmesh.agents.providers.models import ModelProviderCredential
from opsmesh.governance.costs.service import CostAccountingService
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.shared.config import Settings
from opsmesh.shared.security.redaction import is_sensitive_payload_value
from opsmesh.shared.security.secrets import SecretEncryptionService

logger = logging.getLogger(__name__)
_PROVIDER_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")


class AuthorizedModelProvider(ModelProvider):
    """Public ModelProvider extension for tenant-scoped credential references."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        secret_service: SecretEncryptionService,
        *,
        timeout_seconds: int,
    ) -> None:
        self._session_factory = session_factory
        self._secret_service = secret_service
        self._timeout_seconds = timeout_seconds

    def get_model(self, model_name: str | None) -> Model:
        if not activity.in_activity():
            raise RuntimeError("Model credentials may only be resolved in an Activity")
        return ActivityAuthorizedModel(
            AgentModelReference.decode(model_name),
            self._session_factory,
            self._secret_service,
            timeout_seconds=self._timeout_seconds,
        )


class ActivityAuthorizedModel(Model):
    """Authorize without blocking the Worker, close clients and redact failures.

    The public Model extension delegates inference and streaming to the native
    SDK. It supplies product authorization, which the Temporal plugin cannot
    infer from an OpsMesh credential reference.
    """

    def __init__(
        self,
        reference: AgentModelReference,
        sessions: Callable[[], Session],
        secrets: SecretEncryptionService,
        *,
        timeout_seconds: int,
    ) -> None:
        self._reference = reference
        self._sessions = sessions
        self._secrets = secrets
        self._timeout_seconds = timeout_seconds

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        async with self._model() as model:
            response = await model.get_response(*args, **kwargs)
        if self._reference.purpose == "memory":
            await asyncio.to_thread(self._record_memory_usage, response.usage, response.response_id)
        return response

    async def stream_response(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[TResponseStreamEvent]:
        async with self._model() as model:
            async for event in model.stream_response(*args, **kwargs):
                yield event

    @asynccontextmanager
    async def _model(self) -> AsyncIterator[Model]:
        if not activity.in_activity():
            raise RuntimeError("Model credentials may only be resolved in an Activity")
        span = trace.get_current_span()
        span.set_attributes(
            {
                "opsmesh.workspace_id": str(self._reference.workspace_id),
                "opsmesh.run_id": str(self._reference.run_id),
                "opsmesh.agent_profile_id": str(self._reference.agent_profile_id),
            }
        )
        resolved = await asyncio.to_thread(
            AuthorizedModelResolver(self._sessions, self._secrets).resolve, self._reference
        )
        if resolved.api_key is None:
            raise ResourceAccessDeniedError()
        async with AsyncOpenAI(
            api_key=resolved.api_key,
            base_url=resolved.base_url,
            max_retries=0,
            timeout=self._timeout_seconds,
        ) as client:
            model_api = self._reference.model_api
            provider = OpenAIProvider(
                openai_client=client,
                use_responses=None if model_api is None else model_api == OPENAI_RESPONSES_API,
            )
            try:
                yield provider.get_model(self._reference.model)
            except OpenAIError as exc:
                status = exc.status_code if isinstance(exc, APIStatusError) else None
                retryable = (
                    isinstance(exc, APIConnectionError | APITimeoutError)
                    or status in {408, 409, 429}
                    or (status is not None and status >= 500)
                )
                request_id = exc.request_id if isinstance(exc, APIStatusError) else None
                if request_id is not None and (
                    not _PROVIDER_REQUEST_ID.fullmatch(request_id)
                    or is_sensitive_payload_value(request_id)
                ):
                    request_id = None
                model_name = self._reference.model
                if (
                    len(model_name) > 200
                    or "://" in model_name
                    or is_sensitive_payload_value(model_name)
                ):
                    model_name = "[redacted]"
                diagnostic: dict[str, object] = {
                    "error_id": uuid4().hex,
                    "error_type": type(exc).__name__,
                    "model": model_name,
                    "provider": resolved.provider,
                    "status_code": status,
                    "provider_request_id": request_id,
                }
                span.set_attribute("opsmesh.error_id", str(diagnostic["error_id"]))
                info = activity.info()
                # Extract code locations only. Formatting the original exception
                # would include its untrusted message, body and request URL.
                frames = deque(
                    (
                        {
                            "module": frame.f_globals.get("__name__", "unknown"),
                            "function": frame.f_code.co_name,
                            "line": line,
                        }
                        for frame, line in walk_tb(exc.__traceback__)
                    ),
                    maxlen=16,
                )
                logger.error(
                    "Configured model provider request failed",
                    extra={
                        "event_type": "agent.model.failed",
                        "workspace_id": str(self._reference.workspace_id),
                        "run_id": str(self._reference.run_id),
                        "agent_profile_id": str(self._reference.agent_profile_id),
                        "workflow_id": info.workflow_id,
                        "workflow_run_id": info.workflow_run_id,
                        "activity_id": info.activity_id,
                        "activity_type": info.activity_type,
                        "attempt": info.attempt,
                        "retryable": retryable,
                        "exception_frames": list(frames),
                        **diagnostic,
                    },
                )
                # Never put a provider body, endpoint, headers or API key into
                # a Temporal failure or control-plane event.
                raise ApplicationError(
                    "The configured model provider request failed",
                    diagnostic,
                    type="model_provider_failed",
                    non_retryable=not retryable,
                ) from None

    def _record_memory_usage(self, usage: Usage, response_id: str | None) -> None:
        """SDK memory runs do not call the outer Agent's LLM hooks.

        Observe their native responses at the Activity boundary so memory
        generation remains metered without replacing the SDK lifecycle.
        """
        reference = self._reference
        with self._sessions() as session:
            run = session.scalar(
                select(AgentRun)
                .where(
                    AgentRun.workspace_id == reference.workspace_id,
                    AgentRun.id == reference.run_id,
                )
                .with_for_update()
            )
            profile = session.scalar(
                select(AgentProfile).where(
                    AgentProfile.workspace_id == reference.workspace_id,
                    AgentProfile.id == reference.agent_profile_id,
                )
            )
            credential = session.scalar(
                select(ModelProviderCredential).where(
                    ModelProviderCredential.workspace_id == reference.workspace_id,
                    ModelProviderCredential.id == reference.credential_id,
                )
            )
            if run is None or profile is None or credential is None:
                raise ResourceAccessDeniedError()
            normalized = runtime_usage(TypeAdapter(Usage).dump_python(usage, mode="json"))
            if normalized is None:
                raise ValueError("SDK memory model response has no usage structure")
            request = AgentRunRequest(
                agent_profile=AgentProfileSnapshot(
                    profile.id,
                    reference.workspace_id,
                    profile.version,
                    profile.name,
                    profile.role,
                    "",
                    reference.model,
                ),
                context=AgentExecutionContext(reference.workspace_id, run.task_id, run.id),
                input_text="",
                model=reference.model,
                provider=credential.provider,
                model_api=reference.model_api,
            )
            result = AgentRunResult(
                "",
                model_usages=(
                    AgentModelUsageRecord(
                        request_id=response_id or f"sdk-memory:{activity.info().activity_id}",
                        agent=AgentReference(profile.name, profile.id, profile.role),
                        model=reference.model,
                        provider=credential.provider,
                        model_api=reference.model_api,
                        usage=normalized,
                    ),
                ),
            )
            accounting = CostAccountingService(session)
            accounting.record_attempt(
                run=run,
                request=request,
                result=result,
                job_attempt=1,
                request_sequence=1,
                attempt_outcome="succeeded",
                budget_decision=accounting.evaluate_budget(
                    reference.workspace_id, provider=credential.provider, model=reference.model
                ),
            )
            session.commit()


def build_model_provider(
    session_factory: Callable[[], Session], settings: Settings
) -> AuthorizedModelProvider:
    """Create the Worker-side provider used by ``OpenAIAgentsPlugin``."""
    secret = settings.credential_encryption_secret
    key_id = settings.credential_encryption_key_id
    previous = settings.credential_encryption_previous_secrets
    if not isinstance(secret, str) or not isinstance(key_id, str):
        raise ValueError("Credential encryption settings are required for model activities")
    return AuthorizedModelProvider(
        session_factory,
        SecretEncryptionService(secret=secret, key_id=key_id, previous_secrets=previous),
        timeout_seconds=settings.model_request_timeout_seconds,
    )
