"""Current installation, instance and invocation permission intersection."""

from opsmesh.shared.errors import ForbiddenError


def authorize_permissions(
    *,
    ceiling: list[str],
    instance: list[str],
    requested: object,
    permission: str | None = None,
) -> frozenset[str]:
    if not isinstance(requested, (list, tuple)) or any(
        not isinstance(item, str) for item in requested
    ):
        raise ForbiddenError("Invalid invocation permission declaration")
    required = frozenset(requested)
    if not required <= set(ceiling) & set(instance):
        raise ForbiddenError("Invocation permissions were revoked or not granted")
    if permission is not None and permission not in required:
        raise ForbiddenError("The invocation does not grant this host operation")
    return required
