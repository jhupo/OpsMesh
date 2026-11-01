"""Meter and flush SDK 0.24 memory without losing close-time artifacts.

The SDK's manager deliberately logs some generation failures. This pinned adapter
preserves its storage format and extraction code while making failures observable
to the platform finalization barrier. No global function or Runner is patched.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from importlib.metadata import version
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from agents import RunConfig
from agents.models.interface import Model
from agents.sandbox import SandboxAgent, SandboxRunConfig
from agents.sandbox.capabilities import Memory
from agents.sandbox.memory import manager as sdk_memory
from agents.sandbox.memory.prompts import render_memory_consolidation_prompt

from opsmesh.framework.agents import AgentExecutionContext, AgentSpecification
from opsmesh.framework.errors import OpsMeshError

from .models import resolve_profile_model, resolve_settings
from .usage import _numeric_usage

if TYPE_CHECKING:
    from .sandbox_lifecycle import ManagedSandboxSession


async def run_phase_two(
    *,
    config: Any,
    memory_root: str,
    selection: Any,
    run_config: RunConfig,
    run_as: str | None = None,
) -> None:
    """Use the SDK prompt/storage format while retaining the configured sandbox user."""
    from agents import Runner

    options: dict[str, Any] = {
        "name": "sandbox-memory-phase-two",
        "instructions": None,
        "model": config.phase_two_model,
        "run_as": run_as,
    }
    if config.phase_two_model_settings is not None:
        options["model_settings"] = config.phase_two_model_settings
    agent = SandboxAgent(**options)
    prompt = render_memory_consolidation_prompt(
        memory_root=memory_root, selection=selection, extra_prompt=config.extra_prompt
    )
    await Runner.run(agent, prompt, run_config=run_config, max_turns=config.phase_two_max_turns)


class MeteredMemoryModel(Model):
    """Authorize every internal model request and retain unknown failed usage."""

    def __init__(self, delegate: Model, context: AgentExecutionContext, profile: str, stage: str):
        self._delegate, self._context, self.profile, self.stage = delegate, context, profile, stage
        self.model = getattr(delegate, "model", profile)
        self._observations: list[dict[str, Any]] = []

    @property
    def observations(self) -> list[dict[str, Any]]:
        return self._observations

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """Stable SDK capability identity excludes clients, services and mutable usage."""
        return {
            "adapter": "opsmesh-memory-model-v1",
            "model": self.model,
            "profile": self.profile,
            "stage": self.stage,
        }

    async def _observe(self, call_id: str, response: Any = None) -> None:
        usage = getattr(response, "usage", None)
        details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        response_id = getattr(response, "response_id", None)
        item = {
            "call_id": f"memory:{self.stage}:{response_id}" if response_id else call_id,
            "response_id": response_id,
            "run_id": self._context.run_id,
            "model": self.model,
            "model_profile_ref": self.profile,
            "purpose": "memory_generation",
            "stage": self.stage,
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cached_tokens": getattr(details, "cached_tokens", 0),
            "reasoning_tokens": getattr(output_details, "reasoning_tokens", 0),
            "provider_usage": _numeric_usage(usage) if usage is not None else {},
            "total_tokens": getattr(usage, "total_tokens", 0),
            "usage_available": bool(usage is not None and getattr(usage, "requests", 0)),
        }
        self._observations.append(item)
        observer = self._context.services.observe_usage
        if observer is None:
            raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "Native memory requires usage accounting")
        await observer(item)

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        authorize = self._context.services.authorize_model_request
        if authorize is None:
            raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "Native memory requires model admission")
        await authorize(self.profile)
        call_id = f"memory:{self.stage}:{uuid4().hex}"
        try:
            response = await self._delegate.get_response(*args, **kwargs)
        except BaseException:
            await self._observe(call_id)
            raise
        await self._observe(call_id, response)
        return response

    async def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        # SDK memory uses non-streamed Runner.run. Refuse accidental unsupported
        # use before a request, rather than losing the stream's final accounting.
        raise OpsMeshError("FEATURE_UNSUPPORTED", "Memory generation uses non-streamed requests")
        yield  # pragma: no cover - retain the abstract asynchronous iterator signature


class ManagedMemoryGeneration(sdk_memory.SandboxMemoryGenerationManager):
    def __init__(
        self, session: Any, memory: Memory, context: AgentExecutionContext, timeout: float
    ):
        super().__init__(session=session, memory=memory)
        self.context, self.timeout = context, timeout
        self.failure: BaseException | None = None
        self.status = "pending"
        self.clients: list[Any] = []
        self.configuration: dict[str, Any] = {}
        self.run_as: str | None = None

    def _memory_run_config(self) -> RunConfig:
        return RunConfig(
            sandbox=SandboxRunConfig(session=self._session),
            group_id=self.context.session_id,
            workflow_name="opsmesh-native-memory",
            trace_include_sensitive_data=False,
        )

    async def _worker(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is sdk_memory._STOP:
                    return
                await self._process_rollout_file(str(item))
            finally:
                self._queue.task_done()

    async def _run_phase_two(self) -> None:
        if not self._pending_phase_two_rollout_ids:
            return
        selection = await self._storage.build_phase_two_input_selection(
            max_raw_memories_for_consolidation=self._generate_config.max_raw_memories_for_consolidation
        )
        if not await self._storage.rebuild_raw_memories(selected_items=selection.selected):
            raise OpsMeshError("MEMORY_STORAGE_INVALID", "Generated memory inputs disappeared")
        await run_phase_two(
            config=self._generate_config,
            memory_root=self._memory.layout.memories_dir,
            selection=selection,
            run_config=self._memory_run_config(),
            run_as=self.run_as,
        )
        await self._storage.write_phase_two_selection(selected_items=selection.selected)
        self._pending_phase_two_rollout_ids.clear()

    async def flush(self) -> None:
        if self.failure is not None:
            raise OpsMeshError(
                "MEMORY_GENERATION_FAILED", "Native memory generation failed"
            ) from self.failure
        if self.status == "succeeded":
            return
        try:
            async with asyncio.timeout(self.timeout):
                await super().flush()
        except BaseException as exc:
            self.failure, self.status = exc, "failed"
            raise
        self.status = "succeeded"

    async def close_clients(self) -> None:
        clients, self.clients = self.clients, []
        results = await asyncio.gather(
            *(client.close() for client in clients), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    def receipt(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "memories_dir": self.memory.layout.memories_dir,
            "sessions_dir": self.memory.layout.sessions_dir,
            "observations": [
                observation
                for model in (
                    self._generate_config.phase_one_model,
                    self._generate_config.phase_two_model,
                )
                if isinstance(model, MeteredMemoryModel)
                for observation in model.observations
            ],
        }


async def prepare_memory(
    options: dict[str, Any],
    spec: AgentSpecification,
    context: AgentExecutionContext,
    model_resolver: Any = None,
) -> Memory:
    if version("openai-agents") != "0.24.0":
        raise OpsMeshError("CHECKPOINT_INCOMPATIBLE", "Native memory adapter requires SDK 0.24.0")
    owner = cast("ManagedSandboxSession | None", context.resources.sandbox_lifecycle)
    if owner is None:
        raise OpsMeshError(
            "DEPENDENCY_UNAVAILABLE", "Native memory requires a managed sandbox lifecycle"
        )
    if context.services.authorize_model_request is None or context.services.observe_usage is None:
        raise OpsMeshError(
            "DEPENDENCY_UNAVAILABLE", "Native memory requires admission and accounting"
        )
    data = dict(options)
    generation = dict(data.get("generate") or {})
    configuration = {
        **generation,
        "phase_one_model": generation.get("phase_one_model") or spec.model_profile_ref,
        "phase_two_model": generation.get("phase_two_model") or spec.model_profile_ref,
    }
    clients: list[Any] = []
    try:
        for stage, field in (("extract", "phase_one_model"), ("consolidate", "phase_two_model")):
            profile_ref = generation.get(field) or spec.model_profile_ref
            if not isinstance(profile_ref, str) or "@" not in profile_ref:
                raise OpsMeshError(
                    "MEMORY_MODEL_REQUIRED", "Native memory requires versioned model profiles"
                )
            stage_spec = spec.model_copy(
                update={
                    "id": f"{spec.id}-memory-{stage}",
                    "model_profile_ref": profile_ref,
                    "model_settings": {},
                    "tool_choice": None,
                }
            )
            if model_resolver is None:
                model, profile = await resolve_profile_model(stage_spec, context, clients)
            else:
                value = model_resolver(stage_spec, context)
                model = await value if inspect.isawaitable(value) else value
                profile = None
            if not isinstance(model, Model):
                raise OpsMeshError("CONFIG_INVALID", "Memory resolver must return an SDK Model")
            generation[field] = MeteredMemoryModel(model, context, profile_ref, stage)
            generation.setdefault(f"{field}_settings", resolve_settings(stage_spec, profile))
        generation.setdefault("phase_two_max_turns", spec.max_turns)
        data["generate"] = generation
        memory = Memory.model_validate(data)
        key = sdk_memory._memory_layout_key(memory)
        registry = sdk_memory._MEMORY_GENERATION_MANAGERS.setdefault(owner.session, {})
        for existing_key, existing in registry.items():
            if key == existing_key:
                if not isinstance(existing, ManagedMemoryGeneration):
                    raise OpsMeshError("CONFLICT", "Unmanaged memory already owns this layout")
                if existing.configuration != configuration:
                    raise OpsMeshError("CONFLICT", "Memory layout generation policies differ")
                for client in clients:
                    await client.close()
                return memory.model_copy(update={"generate": existing.memory.generate})
            if any(
                a == b or a.startswith(b + "/") or b.startswith(a + "/")
                for a in key
                for b in existing_key
            ):
                raise OpsMeshError("CONFIG_INVALID", "Memory layouts must not overlap")
        assert spec.sandbox is not None
        manager = ManagedMemoryGeneration(
            owner.session, memory, context, spec.sandbox.memory_timeout_seconds
        )
        manager.run_as = spec.sandbox.run_as
        manager.clients = clients
        manager.configuration = configuration
        registry[key] = manager
        owner.memories.append(manager)
        return memory
    except BaseException:
        for client in clients:
            await client.close()
        raise
