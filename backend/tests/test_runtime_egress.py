import pytest

from backend.app.runtime_manager.contracts import RuntimeCreateRequest, RuntimeLimits
from backend.app.runtime_manager.docker_client import (
    _docker_network_environment,
    _docker_network_mode,
)
from backend.app.runtime_manager.egress import RuntimeEgressPolicyError, resolve_egress_policy


def _request(policy: dict[str, object], *, disabled: bool = False) -> RuntimeCreateRequest:
    return RuntimeCreateRequest(
        image="opsmesh-runtime:local",
        name="runtime",
        workspace_id="workspace",
        limits=RuntimeLimits(cpu_count=1, memory_mb=128, disk_mb=256, timeout_seconds=10),
        network_disabled=disabled,
        network_policy=policy,
    )


def test_restricted_egress_requires_gateway_and_normalizes_allowlist() -> None:
    policy = resolve_egress_policy(
        {
            "mode": "restricted",
            "gateway_network": "opsmesh-egress",
            "allowed_domains": ["API.Example.com."],
            "allowed_cidrs": ["203.0.113.7/24"],
            "allowed_ports": [443, 443],
            "allowed_protocols": ["TCP"],
            "proxy_url": "http://egress-gateway:3128",
        },
        forced_disabled=False,
    )

    assert policy.as_dict() == {
        "mode": "restricted",
        "disabled": False,
        "allowed_domains": ["api.example.com"],
        "allowed_cidrs": ["203.0.113.0/24"],
        "allowed_ports": [443],
        "allowed_protocols": ["tcp"],
        "gateway_network": "opsmesh-egress",
        "proxy_url": "http://egress-gateway:3128",
        "enforcement": "docker_network_gateway",
    }
    request = _request(policy.as_dict())
    assert _docker_network_mode(request) == "opsmesh-egress"
    assert _docker_network_environment(request) == {
        "HTTP_PROXY": "http://egress-gateway:3128",
        "HTTPS_PROXY": "http://egress-gateway:3128",
        "ALL_PROXY": "http://egress-gateway:3128",
        "NO_PROXY": "localhost,127.0.0.1",
    }


def test_restricted_egress_fails_closed_without_gateway() -> None:
    with pytest.raises(RuntimeEgressPolicyError) as failure:
        resolve_egress_policy({"mode": "restricted"}, forced_disabled=False)
    assert failure.value.code == "runtime_egress_gateway_required"


def test_forced_network_disable_overrides_requested_egress() -> None:
    policy = resolve_egress_policy(
        {"mode": "internet", "allow_network": True},
        forced_disabled=True,
    )
    assert policy.mode == "none"
    assert policy.disabled is True
    request = _request(policy.as_dict(), disabled=True)
    assert _docker_network_mode(request) == "none"
    assert _docker_network_environment(request) is None
