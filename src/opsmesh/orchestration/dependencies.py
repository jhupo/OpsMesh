"""Composition-owned state and explicit execution operations."""

from fastapi import HTTPException, Request

from opsmesh.orchestration.contracts import RejectingScopeAdmission, ScopeAdmission
from opsmesh.orchestration.runs.activities import FlowActivities
from opsmesh.shared.security.encryption import SecretCipher


def scope_admission(request: Request) -> ScopeAdmission:
    admission = getattr(request.app.state, "execution_scope_admission", None)
    return admission if isinstance(admission, ScopeAdmission) else RejectingScopeAdmission()


def execution_cipher(request: Request) -> SecretCipher:
    cipher = getattr(request.app.state, "secret_cipher", None)
    if not isinstance(cipher, SecretCipher):
        raise HTTPException(503, "Execution is not assembled")
    return cipher


def flow_runtime(request: Request) -> FlowActivities:
    runtime = getattr(request.app.state, "flow_activities", None)
    if not isinstance(runtime, FlowActivities):
        raise HTTPException(503, "Orchestration is not assembled")
    return runtime


def flow_cipher(request: Request) -> SecretCipher:
    cipher = getattr(request.app.state, "secret_cipher", None)
    if not isinstance(cipher, SecretCipher):
        raise HTTPException(503, "Orchestration encryption is not assembled")
    return cipher
