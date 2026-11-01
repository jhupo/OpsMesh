"""Canonical permission names shared by checks and delegation ceilings."""

from fnmatch import fnmatchcase


def canonical_action(action: str) -> str:
    """Preserve deployed secrets:* grants as an alias of the secret:* namespace.

    Resource patterns and departments remain unchanged. This does not introduce
    aliases for other namespaces or turn an exact grant into a wildcard grant.
    """
    if action.startswith("secrets:"):
        return "secret:" + action.removeprefix("secrets:")
    return action


def matches_action(action: str, grant: str) -> bool:
    return fnmatchcase(canonical_action(action), canonical_action(grant))
