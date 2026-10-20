"""Pure policy evaluation; model/content judgement belongs to extensions."""

from opsmesh.platform.policies.schemas import Decision, RiskPolicy


def evaluate_policy(policy: RiskPolicy, *, action: str, name: str) -> Decision:
    matches = [
        rule.decision
        for rule in policy.rules
        if rule.action == action and (rule.name is None or rule.name == name)
    ]
    return max(
        matches or [policy.default], key={"allow": 0, "require_approval": 1, "deny": 2}.__getitem__
    )
