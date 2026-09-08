from dataclasses import dataclass, field

from backend.app.core.typing import string_list
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import RuntimeSpace


def positive_policy_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


@dataclass(frozen=True)
class WorkerJobPolicyDecision:
    allowed: bool
    reason: str | None = None
    code: str | None = None
    details: dict[str, object] = field(default_factory=dict)


def evaluate_worker_job_policy(
    *,
    worker_capabilities: dict[str, object],
    run: AgentRun,
    runtime_space: RuntimeSpace | None = None,
) -> WorkerJobPolicyDecision:
    snapshot = _authorization_snapshot(run)

    tool_decision = _evaluate_allowed_tools(worker_capabilities, snapshot)
    if not tool_decision.allowed:
        return tool_decision

    model_decision = _evaluate_supported_models(worker_capabilities, run, snapshot)
    if not model_decision.allowed:
        return model_decision

    runtime_decision = _evaluate_supported_runtimes(worker_capabilities, snapshot)
    if not runtime_decision.allowed:
        return runtime_decision

    network_decision = _evaluate_network_expectations(
        worker_capabilities,
        snapshot,
        runtime_space,
    )
    if not network_decision.allowed:
        return network_decision

    return WorkerJobPolicyDecision(allowed=True)


def _evaluate_allowed_tools(
    worker_capabilities: dict[str, object],
    snapshot: dict[str, object],
) -> WorkerJobPolicyDecision:
    worker_tools = _optional_string_set(worker_capabilities.get("allowed_tools"))
    if worker_tools is None:
        return WorkerJobPolicyDecision(allowed=True)
    required_tools = set(string_list(snapshot.get("allowed_tools")))
    denied_tools = sorted(required_tools - worker_tools)
    if not denied_tools:
        return WorkerJobPolicyDecision(allowed=True)
    return WorkerJobPolicyDecision(
        allowed=False,
        code="unsupported_tools",
        reason=(
            "Agent run requires tools not allowed by this self-hosted worker: "
            f"{', '.join(denied_tools)}"
        ),
        details={"denied_tools": denied_tools},
    )


def _evaluate_supported_models(
    worker_capabilities: dict[str, object],
    run: AgentRun,
    snapshot: dict[str, object],
) -> WorkerJobPolicyDecision:
    supported_models = string_list(worker_capabilities.get("supported_models"))
    if not supported_models:
        return WorkerJobPolicyDecision(allowed=True)
    model = _run_model(run, snapshot)
    if model is None or _matches_any(model, supported_models):
        return WorkerJobPolicyDecision(allowed=True)
    return WorkerJobPolicyDecision(
        allowed=False,
        code="unsupported_model",
        reason=f"Agent run model is not supported by this self-hosted worker: {model}",
        details={"model": model, "supported_models": supported_models},
    )


def _evaluate_supported_runtimes(
    worker_capabilities: dict[str, object],
    snapshot: dict[str, object],
) -> WorkerJobPolicyDecision:
    supported_runtimes = string_list(worker_capabilities.get("supported_runtimes"))
    if not supported_runtimes:
        return WorkerJobPolicyDecision(allowed=True)
    required_runtimes = _runtime_requirements(snapshot)
    if not required_runtimes or any(
        _matches_any(runtime, supported_runtimes) for runtime in required_runtimes
    ):
        return WorkerJobPolicyDecision(allowed=True)
    return WorkerJobPolicyDecision(
        allowed=False,
        code="unsupported_runtime",
        reason=(
            "Agent run runtime is not supported by this self-hosted worker: "
            f"{', '.join(sorted(required_runtimes))}"
        ),
        details={
            "required_runtimes": sorted(required_runtimes),
            "supported_runtimes": supported_runtimes,
        },
    )


def _evaluate_network_expectations(
    worker_capabilities: dict[str, object],
    snapshot: dict[str, object],
    runtime_space: RuntimeSpace | None,
) -> WorkerJobPolicyDecision:
    supported_modes = _worker_network_modes(worker_capabilities)
    if not supported_modes:
        return WorkerJobPolicyDecision(allowed=True)
    required_modes = _network_requirements(snapshot, runtime_space)
    if not required_modes or required_modes & supported_modes:
        return WorkerJobPolicyDecision(allowed=True)
    return WorkerJobPolicyDecision(
        allowed=False,
        code="unsupported_network_mode",
        reason=(
            "Agent run network mode is not supported by this self-hosted worker: "
            f"{', '.join(sorted(required_modes))}"
        ),
        details={
            "required_network_modes": sorted(required_modes),
            "supported_network_modes": sorted(supported_modes),
        },
    )


def _authorization_snapshot(run: AgentRun) -> dict[str, object]:
    run_input = run.input if isinstance(run.input, dict) else {}
    snapshot = run_input.get("authorization_snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def _run_model(run: AgentRun, snapshot: dict[str, object]) -> str | None:
    model_provider = snapshot.get("model_provider")
    if isinstance(model_provider, dict):
        for key in ("selected_model", "model", "requested_model"):
            value = model_provider.get(key)
            if isinstance(value, str) and value:
                return value
    return run.model if isinstance(run.model, str) and run.model else None


def _runtime_requirements(snapshot: dict[str, object]) -> set[str]:
    runtime_policy = snapshot.get("runtime_policy")
    if not isinstance(runtime_policy, dict):
        return set()
    values: set[str] = set()
    for key in ("provider", "runtime_provider", "runtime_type", "type", "kind", "executor"):
        value = runtime_policy.get(key)
        if isinstance(value, str) and value:
            values.add(_normalize_token(value))
    nested_runtime = runtime_policy.get("runtime")
    if isinstance(nested_runtime, dict):
        for key in ("provider", "type", "kind"):
            value = nested_runtime.get(key)
            if isinstance(value, str) and value:
                values.add(_normalize_token(value))
    return values


def _network_requirements(
    snapshot: dict[str, object],
    runtime_space: RuntimeSpace | None,
) -> set[str]:
    modes: set[str] = set()
    runtime_policy = snapshot.get("runtime_policy")
    if isinstance(runtime_policy, dict):
        modes |= _network_modes_from_policy(runtime_policy)
        mcp_policy = runtime_policy.get("mcp")
        if isinstance(mcp_policy, dict):
            modes |= _network_modes_from_policy(mcp_policy)
    if runtime_space is not None and isinstance(runtime_space.network_policy, dict):
        modes |= _network_modes_from_policy(runtime_space.network_policy)
    return modes


def _worker_network_modes(worker_capabilities: dict[str, object]) -> set[str]:
    modes: set[str] = set()
    for key in ("supported_network_modes", "network_expectations"):
        modes |= {
            _normalize_network_mode(mode)
            for mode in string_list(worker_capabilities.get(key))
        }
    for key in ("network_mode", "network"):
        value = worker_capabilities.get(key)
        if isinstance(value, str):
            modes.add(_normalize_network_mode(value))
        elif isinstance(value, dict):
            modes |= _network_modes_from_policy(value)
    return {mode for mode in modes if mode}


def _network_modes_from_policy(policy: dict[str, object]) -> set[str]:
    modes: set[str] = set()
    for key in ("mode", "network_mode", "egress_mode"):
        value = policy.get(key)
        if isinstance(value, str) and value:
            modes.add(_normalize_network_mode(value))
    for key in ("network", "network_policy"):
        value = policy.get(key)
        if isinstance(value, str) and value:
            modes.add(_normalize_network_mode(value))
        elif isinstance(value, dict):
            modes |= _network_modes_from_policy(value)
    allow_egress = policy.get("allow_network_egress")
    if isinstance(allow_egress, bool):
        modes.add("internet" if allow_egress else "none")
    return {mode for mode in modes if mode}


def _optional_string_set(value: object) -> set[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def _matches_any(value: str, patterns: list[str]) -> bool:
    normalized = _normalize_token(value)
    for pattern in patterns:
        normalized_pattern = _normalize_token(pattern)
        if normalized_pattern == "*":
            return True
        if normalized_pattern.endswith("*") and normalized.startswith(normalized_pattern[:-1]):
            return True
        if normalized == normalized_pattern:
            return True
    return False


def _normalize_network_mode(value: str) -> str:
    normalized = _normalize_token(value)
    if normalized in {"off", "offline", "disabled", "disable", "none", "no_network", "deny"}:
        return "none"
    if normalized in {"egress", "online", "public", "web"}:
        return "internet"
    if normalized in {"allowlist", "allow_list", "restricted_egress"}:
        return "restricted"
    return normalized


def _normalize_token(value: str) -> str:
    return value.strip().lower().replace("-", "_")
