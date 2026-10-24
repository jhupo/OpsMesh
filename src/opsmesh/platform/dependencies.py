"""HTTP access to execution ports assembled for platform consumers."""

from fastapi import HTTPException, Request

from opsmesh.platform.targets import TargetPorts


def ports(request: Request) -> TargetPorts:
    value = getattr(request.app.state, "automation_targets", None)
    if not isinstance(value, TargetPorts):
        raise HTTPException(503, "Automation execution is not configured")
    return value
