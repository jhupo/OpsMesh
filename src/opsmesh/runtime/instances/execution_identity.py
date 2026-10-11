"""Immutable process identity and policy at the trusted Runtime boundary."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RuntimeExecutionIdentity:
    allocation_id: UUID
    uid: int
    network_policy: dict[str, object]

    def payload(self) -> dict[str, object]:
        return {
            "allocation_id": str(self.allocation_id),
            "uid": self.uid,
            "network_policy": self.network_policy,
        }

    def environment(self) -> dict[str, str]:
        # Every variable is explicit so an execution cannot inherit permissions
        # from the container or a previous execution using the same numeric UID.
        proxy = (
            f"http://127.0.0.1:{20_000 + self.uid - 100_000}"
            if self.network_policy.get("mode") == "restricted"
            else ""
        )
        return {
            "HOME": f"/tmp/opsmesh-runs/{self.uid}",
            "TMPDIR": f"/tmp/opsmesh-runs/{self.uid}",
            "HTTP_PROXY": proxy,
            "HTTPS_PROXY": proxy,
            "ALL_PROXY": proxy,
            "http_proxy": proxy,
            "https_proxy": proxy,
            "all_proxy": proxy,
            "NO_PROXY": "",
            "no_proxy": "",
        }
