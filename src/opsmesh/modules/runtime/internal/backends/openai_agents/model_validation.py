"""Deterministic API compatibility and deployment-declared model capabilities."""

from typing import Any

from opsmesh.framework.agents import AgentSpecification, merge_model_settings
from opsmesh.framework.errors import OpsMeshError


def validate_model_configuration(spec: AgentSpecification, profile: dict[str, Any]) -> None:
    settings = merge_model_settings(profile.get("settings", {}), spec.model_settings)
    missing: set[str] = set()
    required = {"text"}
    if (
        spec.tools
        or spec.agent_tools
        or spec.handoffs
        or spec.mcp_refs
        or spec.skill_refs
        or spec.knowledge_collections
        or spec.hosted_tools
        or spec.native_tools
        or spec.sandbox
    ):
        required.add("tools")
    if spec.output_schema is not None or spec.output_type_ref:
        required.add("structured_output")
    if "features" in profile:
        missing.update(required - set(profile["features"]))
    populated = {key for key, value in settings.items() if value is not None}
    supported = profile.get("supported_settings")
    if supported is not None:
        missing.update(f"setting:{key}" for key in populated - set(supported))
    missing.update(
        f"setting:{key}" for key in populated & set(profile.get("unsupported_settings", ()))
    )
    if profile.get("api", "responses") == "chat_completions":
        for field in ("truncation", "response_include", "context_management"):
            if settings.get(field) is not None:
                missing.add(field)
        reasoning = settings.get("reasoning") or {}
        if isinstance(reasoning, dict):
            missing.update(
                f"reasoning.{key}"
                for key, value in reasoning.items()
                if key != "effort" and value is not None
            )
        if spec.prompt is not None or spec.prompt_ref or spec.prompt_template_ref:
            missing.add("prompt")
        if spec.hosted_tools:
            missing.add("hosted_tools")
        if spec.native_tools:
            missing.add("native_tools")
        if spec.sandbox:
            missing.add("sandbox")
        if spec.session_compaction:
            missing.add("session_compaction")
        if spec.model_transport == "websocket":
            missing.add("websocket")
    if missing:
        raise OpsMeshError(
            "FEATURE_UNSUPPORTED",
            "Model profile cannot implement the requested features or settings",
            details={"features": sorted(missing), "api": profile.get("api", "responses")},
        )
