from __future__ import annotations

from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.self_hosted.models import RuntimeCredential, SelfHostedWorker


def self_hosted_trust_state(
    worker: SelfHostedWorker,
    runtime: WorkspaceRuntime,
    credential: RuntimeCredential | None,
) -> str:
    if credential is not None and credential.status == "revoked":
        return "revoked"
    if worker.status == "revoked" or runtime.status == "revoked":
        return "revoked"
    if worker.status == "quarantined" or runtime.status == "quarantined":
        return "quarantined"
    if worker.status == "degraded" or runtime.connection_status == "degraded":
        return "degraded"
    if worker.status in {"offline", "disabled"} or runtime.connection_status == "offline":
        return "offline"
    return "active"


def self_hosted_machine_warning(
    trust_state: str,
    *,
    stale: bool,
) -> tuple[str | None, str | None]:
    if trust_state == "revoked":
        return "credential_revoked", "Machine credential is revoked."
    if trust_state == "quarantined":
        return "machine_quarantined", "Machine is quarantined and cannot accept jobs."
    if trust_state == "degraded":
        return "machine_degraded", "Machine is degraded and cannot accept jobs."
    if trust_state == "offline":
        return "machine_offline", "Machine is offline."
    if stale:
        return "heartbeat_stale", "Machine heartbeat is stale."
    return None, None


def self_hosted_remediation_actions(
    trust_state: str,
    *,
    stale: bool,
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    if stale:
        actions.append(
            {
                "code": "check_runner_heartbeat",
                "label": "Check runner heartbeat",
                "severity": "warning",
                "description": (
                    "Verify the self-hosted runner process is online and can reach the API."
                ),
            }
        )
    if trust_state == "degraded":
        actions.extend(
            [
                {
                    "code": "restart_runner",
                    "label": "Restart runner",
                    "severity": "warning",
                    "description": (
                        "Restart the local worker and confirm its policy capabilities match "
                        "the workspace."
                    ),
                },
                {
                    "code": "review_machine_policy",
                    "label": "Review machine policy",
                    "severity": "warning",
                    "description": (
                        "Check allowed tools, runtime spaces, network modes, and capacity "
                        "limits."
                    ),
                },
            ]
        )
    elif trust_state == "quarantined":
        actions.append(
            {
                "code": "review_quarantine_reason",
                "label": "Review quarantine",
                "severity": "critical",
                "description": (
                    "Inspect security events and only restore the runner after the issue is "
                    "resolved."
                ),
            }
        )
    elif trust_state == "revoked":
        actions.append(
            {
                "code": "rotate_runtime_credential",
                "label": "Rotate credential",
                "severity": "critical",
                "description": "Issue a new runtime credential and re-enroll the local machine.",
            }
        )
    elif trust_state == "offline":
        actions.append(
            {
                "code": "start_runner",
                "label": "Start runner",
                "severity": "warning",
                "description": (
                    "Start the local worker service or reconnect the machine to the network."
                ),
            }
        )
    return actions
