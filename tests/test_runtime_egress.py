from dataclasses import replace

import pytest

from opsmesh.runtime.backends.docker import _docker_network_mode
from opsmesh.runtime.instances.contracts import RuntimeCreateRequest, RuntimeLimits
from opsmesh.runtime.instances.policies.egress import (
    RuntimeEgressPolicyError,
    resolve_egress_policy,
)
from opsmesh.shared.security.egress import (
    EgressUrlPolicy,
    EgressUrlValidationError,
    url_host,
    validate_egress_url,
)


def _request(policy: dict[str, object], *, disabled: bool = False) -> RuntimeCreateRequest:
    return RuntimeCreateRequest(
        image="opsmesh-runtime:local",
        name="runtime",
        workspace_id="workspace",
        limits=RuntimeLimits(cpu_count=1, memory_mb=128, disk_mb=256, timeout_seconds=10),
        network_disabled=disabled,
        network_policy=policy,
    )


def test_restricted_egress_normalizes_allowlist_without_container_network_group() -> None:
    policy = resolve_egress_policy(
        {
            "mode": "restricted",
            "allowed_domains": ["API.Example.com."],
            "allowed_cidrs": ["203.0.113.7/24"],
            "allowed_ports": [443, 443],
            "allowed_protocols": ["TCP"],
        },
        forced_disabled=False,
    )

    assert policy.as_dict() == {
        "mode": "restricted",
        "allowed_domains": ["api.example.com"],
        "allowed_cidrs": ["203.0.113.0/24"],
        "allowed_ports": [443],
        "allowed_protocols": ["tcp"],
    }
    request = _request(policy.as_dict())
    request = replace(request, shared_host=True)
    assert _docker_network_mode(request) == "bridge"


def test_restricted_egress_fails_closed_without_destinations() -> None:
    with pytest.raises(RuntimeEgressPolicyError) as failure:
        resolve_egress_policy({"mode": "restricted"}, forced_disabled=False)
    assert failure.value.code == "runtime_egress_destinations_required"


def test_forced_network_disable_overrides_requested_egress() -> None:
    policy = resolve_egress_policy(
        {"mode": "internet"},
        forced_disabled=True,
    )
    assert policy.mode == "none"
    assert policy.disabled is True
    request = _request(policy.as_dict(), disabled=True)
    assert _docker_network_mode(request) == "none"


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@example.com/api",
        "https://example.com/api?token=secret",
        "https://example.com/api#secret",
    ],
)
def test_egress_url_rejects_unsafe_configuration(url: str) -> None:
    with pytest.raises(EgressUrlValidationError, match="URL"):
        validate_egress_url(
            url,
            policy=EgressUrlPolicy(allowed_schemes=frozenset({"https"})),
        )


def test_url_host_never_returns_user_information() -> None:
    assert url_host("https://user:password@example.com:8443/api") == "example.com:8443"
