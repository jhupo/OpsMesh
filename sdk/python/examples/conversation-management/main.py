"""One scoped tool for submitting operations and reading their durable receipts."""

from uuid import UUID

from opsmesh_sdk import PluginContext, get_invocation
from opsmesh_sdk.channel_management import ManagementRequest, OperationReceipt


async def manage(
    request: ManagementRequest | None = None, operation_id: UUID | None = None
) -> OperationReceipt:
    invocation = get_invocation()
    if invocation.management is None:
        raise ValueError("The host has not supplied a conversation management capability")
    if (request is None) == (operation_id is None):
        raise ValueError("Supply an operation request or a receipt ID")
    if request is not None:
        return await invocation.management.submit(request)
    assert operation_id is not None
    return await invocation.management.get(operation_id)


def setup(ctx: PluginContext) -> None:
    ctx.tool(
        "manage",
        manage,
        description=(
            "Submit an authorized operation for the current conversation, or read its receipt. "
            "Pending and uncertain receipts are not proof of success."
        ),
        effects=("conversation_management",),
    )
