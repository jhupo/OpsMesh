"""SDK sandbox objects stay entirely inside the OpenAI adapter."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from agents.sandbox import (
    LocalSnapshotSpec,
    Manifest,
    SandboxArchiveLimits,
    SandboxConcurrencyLimits,
    SandboxRunConfig,
)
from agents.sandbox.capabilities import Compaction, Filesystem, Memory, Shell, Skills

from opsmesh.framework.agents import AgentExecutionContext, AgentSpecification, SandboxConfiguration
from opsmesh.framework.errors import OpsMeshError


def capabilities(spec: SandboxConfiguration) -> list[Any]:
    # Revalidate a snapshot: frozen contracts may still contain mutable option dictionaries.
    spec = SandboxConfiguration.model_validate(spec.model_dump(mode="python"))
    known: dict[str, Any] = {
        "shell": Shell,
        "filesystem": Filesystem,
        "compaction": Compaction,
        "skills": Skills,
        "memory": Memory,
    }
    result: list[Any] = [known[name]() for name in spec.capabilities]
    for options in spec.capability_options:
        options = dict(options)
        kind = options.pop("type", None)
        if kind == "memory" and options.get("generate", {}) is not None:
            raise OpsMeshError(
                "DEPENDENCY_UNAVAILABLE", "Generating memory requires prepare_capabilities"
            )
        if kind not in known:
            raise OpsMeshError("feature_unsupported", "Unsupported sandbox capability")
        result.append(known[kind].model_validate(options))
    return result


async def prepare_capabilities(
    specification: AgentSpecification,
    context: AgentExecutionContext,
    clients: list[Any],
    model_resolver: Any = None,
) -> list[Any]:
    from .sandbox_memory import prepare_memory

    assert specification.sandbox is not None
    spec = SandboxConfiguration.model_validate(specification.sandbox.model_dump(mode="python"))
    plain = spec.model_copy(update={"capability_options": ()})
    result = capabilities(plain)
    for options in spec.capability_options:
        if options.get("type") == "memory" and options.get("generate", {}) is not None:
            result.append(await prepare_memory(options, specification, context, model_resolver))
        else:
            single = spec.model_copy(update={"capabilities": (), "capability_options": (options,)})
            result.extend(capabilities(single))
    return result


def build_sandbox(spec: SandboxConfiguration, context: AgentExecutionContext) -> SandboxRunConfig:
    spec = SandboxConfiguration.model_validate(spec.model_dump(mode="python"))
    manifest = Manifest.model_validate(spec.manifest)
    values: dict[str, Any] = {
        "manifest": manifest,
        "cwd": spec.cwd,
        "concurrency_limits": SandboxConcurrencyLimits(
            manifest_entries=spec.manifest_concurrency, local_dir_files=spec.file_concurrency
        ),
        "archive_limits": SandboxArchiveLimits(**spec.archive_limits),
    }
    if context.services.get("openai_sandbox_session") is not None:
        values["session"] = context.services["openai_sandbox_session"]
        return SandboxRunConfig(**values)
    if context.services.get("openai_sandbox_client") is not None:
        values["client"] = context.services["openai_sandbox_client"]
        values["options"] = context.services.get("openai_sandbox_options")
        if spec.session_state is not None:
            values["session_state"] = values["client"].deserialize_session_state(spec.session_state)
        return SandboxRunConfig(**values)
    if spec.snapshot_path:
        values["snapshot"] = LocalSnapshotSpec(base_path=Path(spec.snapshot_path))
    if spec.provider == "injected":
        # Only trusted backend extension code may install native SDK objects here.
        session = context.services.get("openai_sandbox_session")
        client = context.services.get("openai_sandbox_client")
        if session is None and client is None:
            raise OpsMeshError("dependency_unavailable", "No SDK sandbox session/client injected")
        if session is not None:
            values["session"] = session
        else:
            values["client"] = client
    elif spec.provider == "local":
        if sys.platform == "win32":
            raise OpsMeshError("feature_unsupported", "UnixLocal sandbox is unavailable on Windows")
        from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient

        values["client"] = UnixLocalSandboxClient()
        if spec.options:
            raise OpsMeshError(
                "feature_unsupported", "Local sandbox creation options not supported"
            )
    elif spec.provider == "docker":
        from .sandbox_docker import docker_client

        values["client"], values["options"] = docker_client(spec.options)
    else:
        raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "Sandbox provider is not registered")
    if spec.session_state is not None:
        if "session" in values:
            raise OpsMeshError("config_invalid", "Live session and explicit resume state conflict")
        values["session_state"] = values["client"].deserialize_session_state(spec.session_state)
    return SandboxRunConfig(**values)
