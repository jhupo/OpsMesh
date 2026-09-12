from sqlalchemy.orm import Session

from backend.app.admin.policy_reader import PlatformPolicyService


class SelfHostedPolicyGate:
    def __init__(self, session: Session) -> None:
        self._session = session

    def require_enabled(self) -> None:
        policy = PlatformPolicyService(self._session).risky_execution_policy()
        if not policy.allow_self_hosted_runtimes:
            raise ValueError("Self-hosted runtimes are disabled by platform safety policy")
