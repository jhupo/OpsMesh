"""OpsMeshMatcher participation checks, separate from platform resource authorization."""

from __future__ import annotations

from .dependencies import DependencyScope
from .rule import Rule


class Permission(Rule):
    """Use &, | and ~ to compose response permissions."""

    async def __call__(self, scope: DependencyScope) -> bool:
        if not self.checks:
            return True
        for check in self.checks:
            if await scope.invoke(check):
                return True
        return False
