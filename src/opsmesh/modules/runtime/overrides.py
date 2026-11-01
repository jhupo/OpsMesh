"""Public, reference-only run overrides; published agent definitions remain unchanged."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from opsmesh.framework.agents import (
    AgentSpecification,
    ModelSettingsOverrides,
    merge_model_settings,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract


class SandboxRunOverrides(OpsMeshContract):
    snapshot_ref: str | None = Field(default=None, min_length=1, max_length=200)
    session_ref: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def select_source(self) -> Self:
        if bool(self.snapshot_ref) == bool(self.session_ref):
            raise ValueError("Select exactly one registered snapshot or sandbox session")
        return self


class RunOverrides(OpsMeshContract):
    workspace_id: str | None = Field(default=None, min_length=1, max_length=200)
    sandbox: SandboxRunOverrides | None = None
    model_settings: ModelSettingsOverrides | None = None
    tool_choice: Literal["none"] | None = None

    @model_validator(mode="after")
    def select_workspace(self) -> Self:
        if self.workspace_id is not None and self.sandbox is not None:
            raise ValueError("Choose a workspace or a sandbox source reference, not both")
        return self


def resolve_run_agent(published: AgentSpecification, overrides: RunOverrides) -> AgentSpecification:
    """Return a fresh effective root agent; authorization belongs to the submitting service."""

    overrides = RunOverrides.model_validate(overrides.model_dump(mode="json"))
    data = published.model_dump(mode="json")
    if overrides.tool_choice == "none":
        data.update(tool_choice="none", reset_tool_choice=False)
    if overrides.workspace_id is not None:
        data["workspace_id"] = overrides.workspace_id
        if data["sandbox"]:
            _clear_saved_source(data["sandbox"])
    if overrides.sandbox is not None:
        if data["sandbox"] is None:
            raise OpsMeshError("CONFIG_INVALID", "Sandbox overrides require a sandbox agent")
        _clear_saved_source(data["sandbox"])
        data["sandbox"].update(overrides.sandbox.model_dump(exclude_none=True))
        data["workspace_id"] = None  # The authorized stored source owns workspace selection.
    if overrides.model_settings is not None:
        data["model_settings"] = merge_model_settings(
            data["model_settings"],
            overrides.model_settings.model_dump(mode="json", exclude_none=True),
        )
    return AgentSpecification.model_validate(data)


def _clear_saved_source(configuration: dict[str, object]) -> None:
    for name in ("snapshot_ref", "session_ref", "snapshot_path", "session_state"):
        configuration[name] = None
